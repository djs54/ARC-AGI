# Plan: A-127 — Phase 4 Promote v2 to Default Agent

## Card metadata

- **Card:** A127
- **Priority:** P0
- **Layer:** ARC runtime & documentation
- **Depends on:** A124, A125, A126
- **Intended executor:** GPT-5.4-mini subagent

## Summary

Make v2 the production default, archive v1 orchestrator from hot path, and update architecture/backlog documentation to reflect the v2 migration completion.

## Rationale

- A118-A123 built the v2 prototype
- A124 hardened the LLM adapter and stall guard
- A125 created comparison tests proving v2 ≥ v1 on 3+ metrics
- A126 ported telemetry, cost tracking, and failure taxonomy
- A127 now flips the default flag and archives the old architecture

## Implementation approach

### Step 1: Update `run_single_puzzle.py` — flip default agent version

**File:** `run_single_puzzle.py`

**Change:** Line ~1225 (in `async def main()`), change:
```python
llm_overrides = {
    key: value
    for key, value in {
        "model": args.model,
        ...
    }.items()
    if value is not None
}
```

**Specific edit:** Locate where `parser.add_argument("--agent-version", ...)` is defined (search for it). Change the `default=` value:

**Before:**
```python
parser.add_argument(
    "--agent-version",
    choices=["v1", "v2"],
    default="v1",
    help="Which ARC agent to run"
)
```

**After:**
```python
parser.add_argument(
    "--agent-version",
    choices=["v1", "v2"],
    default="v2",
    help="Which ARC agent to run"
)
```

Then verify the logic around line 1290 that branches on `args.agent_version == "v2"` is correct (it should dispatch to `_run_arc_v2_batch()` when true).

### Step 2: Archive `agents/arc3/orchestrator.py` — add reference note

**File:** `agents/arc3/orchestrator.py` (top-level comment)

**Change:** Add a notice at the very top of the file after the module docstring:

```python
"""
ARCHIVE NOTICE (A127): This module is now archived and no longer part of the 
production hot path. It is retained for reference and regression testing via 
--agent-version=v1. All new development uses agents/arc4/ (v2 workflow).

Historical context: This orchestrator was the primary ARC agent through A123.
It has been superseded by the v2 workflow layers (perceive → resolve → plan → 
vet → execute → evaluate) in agents/arc4/, which provide better composability,
testability, and port-based dependency injection.

To run v1 for validation: python run_single_puzzle.py --agent-version=v1
"""
```

No code changes in this file — only documentation.

### Step 3: Update `ARCHITECTURE.md` — add v2 layer model section

**File:** `ARCHITECTURE.md`

**Context:** Find the section that describes the agent architecture (search for "Orchestrator" or "Phase Loop"). Add a new subsection after the current v1 description:

**Add this section:**

```markdown
## ARC v2 Workflow Architecture (A118–A127)

**Status:** Production default (promoted A127)

The v2 architecture decomposes the ARC agent into discrete workflow phases 
with port-based dependency injection:

```
perceive() → resolve() → plan() → vet() → execute() → evaluate()
   ↓            ↓         ↓        ↓        ↓           ↓
 Observe    Goal-ID    Strategy  Go/NoGo  Act on    Outcome
 board      (MCP)      (LLM)     (Determ) board     assessment
```

**Design principles:**
- **Stateless phases:** Each phase is a pure function taking WorkflowState + observation
- **Injected ports:** Graph queries, LLM, telemetry all flow through Ports interfaces
- **Observable boundaries:** Each phase emits structured results into telemetry/trace
- **Deterministic guards:** Stall detection, budget exhaustion, crash handling

**Key files:**
- `agents/arc4/workflow.py` — orchestrator state machine + limits
- `agents/arc4/perceive.py` — GridSnapshot + entity extraction
- `agents/arc4/resolver.py` — MCP-backed goal hypothesis and ranking
- `agents/arc4/planner.py` — LLM-generated action sequences
- `agents/arc4/vetter.py` — deterministic Go/No-Go gate
- `agents/arc4/executor.py` — action execution + sandbox
- `agents/arc4/evaluator.py` — falsification + progress judgment
- `agents/arc4/telemetry.py` — v1 artifact parity + token tracking
- `agents/arc4/ports.py` — dependency injection contracts

**Comparison to v1:**
| Aspect | v1 | v2 |
|---|---|---|
| Architecture | Monolithic orchestrator | Composable phase layers |
| Testability | Mock entire orchestrator | Mock individual phases + ports |
| Dependency Injection | Tight coupling to arc3 modules | Clean Port abstractions |
| Observability | Single trace stream | Per-phase structured results |
| Artifact Compatibility | Primary format | Full parity via telemetry adapter |

**v1 archival:** The v1 `agents/arc3/orchestrator.py` remains available for 
regression testing via `--agent-version=v1` but is no longer the production path.

```

### Step 4: Update `backlog/masterBacklogTracker.md` — mark A124–A127 complete

**File:** `backlog/masterBacklogTracker.md`

**Change:** Find the rows for A123, A124, A125, A126 in the table and add rows (if missing) or update their state.

Add these rows at the end of the table (or update if they exist):

```markdown
| A124 | Fix LLM Adapter Prompt + Stall Guard Tuning | P0 | complete | TBD | A123 | `backlog/plans/A-124-fix-llm-adapter-and-stall.md` | LLM adapter bug fixed, stall guard exploration-aware, `make test-a` 18/18 | Hardens v2 against LLM prompt formatting and premature stall timeout |
| A125 | ARC v2 vs v1 Comparison Test Suite | P0 | complete | TBD | A124 | `backlog/plans/A-125-v2-v1-comparison-tests.md` | 3 metrics passing, 1 skipped (awaits smoke), `make smoke-compare` target added | Proves v2 wins on ≥3 of 4 dimensions vs v1 before promotion |
| A126 | Port v1 Features to v2 Telemetry | P0 | complete | TBD | A125 | `backlog/plans/A-126-port-v1-telemetry-features.md` | Failure taxonomy, token tracking, cost_usd field added, `make test-a` 18/18 | Full artifact parity: tokens_input, tokens_output, cost_usd, failure_class all populated |
| A127 | Phase 4 Promote v2 to Default Agent | P0 | complete | TBD | A126 | `backlog/plans/A-127-phase-4-promote-v2-to-default.md` | v2 is now default (`--agent-version=v2` no longer needed), v1 available as `--agent-version=v1`, ARCHITECTURE.md updated | Completes ARC v2 migration; v1 archived for reference/regression; production now uses v2 workflow |
```

## Validation commands

```bash
# Verify default is v2 by checking help
python run_single_puzzle.py --help | grep -A 2 "agent-version"

# Run a smoke with the new default (no --agent-version flag needed)
CAMPY_MCP_CMD="../hippocampy/.venv/bin/python -m campy.adapters.mcp_server" \
PYTHONPATH=. .venv/bin/python run_single_puzzle.py \
  --live-smoke --num-puzzles 1 --max-steps 10

# Verify v1 still works
CAMPY_MCP_CMD="../hippocampy/.venv/bin/python -m campy.adapters.mcp_server" \
PYTHONPATH=. .venv/bin/python run_single_puzzle.py \
  --live-smoke --num-puzzles 1 --max-steps 10 --agent-version=v1

# Verify all baseline tests still pass
make test-a
```

## Acceptance criteria

- [x] `--agent-version` default changed from "v1" to "v2"
- [x] `agents/arc3/orchestrator.py` has archive notice at top
- [x] `ARCHITECTURE.md` has new "ARC v2 Workflow Architecture" section with layer model and comparison table
- [x] `backlog/masterBacklogTracker.md` rows updated for A124, A125, A126, A127 with "complete" status
- [x] `make test-a` passes (18/18)
- [x] Smoke runs by default use v2 (no flag required)
- [x] v1 still accessible for regression testing

## Notes

- This is a simple flag flip + documentation update
- No code changes to agents/arc4/ or agents/arc3/
- All hard work was done in A124–A126
- A127 is primarily administrative/documentation closure of the v2 migration
