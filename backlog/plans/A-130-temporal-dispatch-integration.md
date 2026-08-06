# A-130 — Wire Temporal Dispatch Into run_single_puzzle.py With Feature-Flagged Fallback

Card: backlog/A130.md

## Summary

Integration card: wire the Temporal dispatch path into `run_single_puzzle.py` with feature-flagged inline fallback matching the Sandbox pattern. Add CLI flag, local-loop runbook, and smoke validation.

## Implementation Approach

### 1. Dispatch Branch in `run_single_puzzle.py`

Add the Temporal dispatch path alongside the existing inline path. The pattern mirrors Sandbox's `dispatch_workflow()`:

```python
# At top (guarded import):
try:
    from agents.arc4.temporal_client import is_temporal_enabled, start_arc_workflow
    HAS_TEMPORAL = True
except ImportError:
    HAS_TEMPORAL = False

# In argument parser:
parser.add_argument("--temporal", action="store_true",
                    help="Enable Temporal.io workflow dispatch (requires running Temporal server)")

# In main execution path:
use_temporal = HAS_TEMPORAL and (is_temporal_enabled() or args.temporal)

if use_temporal:
    import asyncio
    handle = asyncio.get_event_loop().run_until_complete(
        start_arc_workflow(task_id, first_observation, state.to_dict())
    )
    if handle is not None:
        result_dict = asyncio.get_event_loop().run_until_complete(handle.result())
        result = WorkflowRunResult.from_dict(result_dict)
        logger.info("Puzzle completed via Temporal workflow")
    else:
        logger.info("Temporal unavailable, falling back to inline execution")
        result = orchestrator.run(state, first_observation)
else:
    result = orchestrator.run(state, first_observation)
```

Key behaviors:
- `temporalio` not installed → `HAS_TEMPORAL = False` → always inline
- `ARC_TEMPORAL_ENABLED=0` (default) and no `--temporal` flag → always inline
- `ARC_TEMPORAL_ENABLED=1` or `--temporal` but Temporal unreachable → `handle is None` → inline fallback with log message
- `ARC_TEMPORAL_ENABLED=1` and Temporal reachable → dispatch through Temporal

### 2. Telemetry Parity

After receiving the Temporal result, write the same telemetry artifacts as the inline path:
- `agent_execution_trace.json`
- Per-phase timing in the trace
- `cost_usd`, `tokens_input`, `tokens_output` fields
- `failure_class` if applicable

The `WorkflowRunResult.from_dict()` deserializes the Temporal result into the same structure, so existing telemetry emission code works unchanged.

### 3. Makefile Target

```makefile
smoke-temporal: temporal-up
	@echo "Waiting for Temporal to be ready..."
	@sleep 5
	ARC_TEMPORAL_ENABLED=1 python -m agents.arc4.temporal_worker &
	@sleep 2
	ARC_TEMPORAL_ENABLED=1 python run_single_puzzle.py --agent-version=v2 --task-id=demo --temporal
	@kill %1 2>/dev/null || true
	$(MAKE) temporal-down
```

### 4. Local-Loop Runbook (`docs/runbooks/temporal-local-loop.md`)

```markdown
# ARC v2 Temporal Local Loop

## Prerequisites

- Docker running locally
- `pip install -e '.[temporal]'`

## Start Sequence

# Terminal 1: Start Temporal infrastructure
make temporal-up

# Terminal 2: Start Temporal worker
ARC_TEMPORAL_ENABLED=1 python -m agents.arc4.temporal_worker

# Terminal 3: Run a puzzle through Temporal
ARC_TEMPORAL_ENABLED=1 python run_single_puzzle.py --agent-version=v2 --task-id=demo --temporal

## Inspect

# Temporal UI (workflow history, per-activity timing):
open http://localhost:8233

# Run result:
cat results/demo/agent_execution_trace.json | jq '.status, .completed_cycles'

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| ARC_TEMPORAL_ENABLED | 0 | Enable Temporal dispatch |
| ARC_TEMPORAL_TARGET | localhost:7233 | Temporal frontend |
| ARC_TEMPORAL_NAMESPACE | default | Temporal namespace |
| ARC_TEMPORAL_TASK_QUEUE | arc-agent | Worker task queue |

## Troubleshooting

- **Connection refused on :7233** — `make temporal-up` and wait ~10s for startup
- **Worker not polling** — check task queue name matches between worker and client
- **Activity timeout** — default 5 min per phase; increase via code if LLM is slow
- **Fallback to inline** — expected when Temporal is down; check logs for "Temporal unavailable"

## Teardown

make temporal-down
```

## Tests to Add

1. **`tests/test_a130_temporal_dispatch.py`**
   - `test_inline_fallback_when_temporal_disabled` — verify `--temporal` not set → inline path
   - `test_inline_fallback_when_temporal_unreachable` — verify graceful fallback
   - `test_cli_temporal_flag_parsed` — verify `--temporal` is a valid argument
   - `test_result_structure_matches_inline` — compare telemetry artifact keys

## Validation Commands

```bash
make test-a
pytest tests/test_a130_temporal_dispatch.py -v
# Full smoke (requires Docker):
make smoke-temporal
```

## Assumptions

- The Temporal dispatch path is async; `run_single_puzzle.py` may need `asyncio.run()` or event loop management
- Telemetry artifacts are written from the same `WorkflowRunResult` regardless of dispatch path
- The `make smoke-temporal` target is a convenience — CI can skip it if Docker isn't available
- No changes to the MCP seam — Temporal activities call phases that call MCP tools through the existing `GraphQueryPort`
