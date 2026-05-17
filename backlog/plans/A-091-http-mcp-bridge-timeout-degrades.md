# Plan: A-091 — HTTP MCP bridge timeout degrades

## Card metadata

- **Card:** A091
- **Priority:** P0
- **Layer:** transport/client seam
- **Depends on:** A064, A075, A078, A084

## Summary

Keep the HTTP MCP bridge path, but make it fault-tolerant. ARC should still communicate through the MCP client seam, while transport failures from the SideQuests adapter-to-daemon HTTP leg become explicit memory degradation signals instead of step-0 task crashes.

This preserves the third-party-tool simulation:

```text
ARC runtime -> MCP client -> SideQuests MCP adapter -> HTTP /mcp -> brain daemon
```

The fix is to classify bridge failures as memory transport failures and convert them into bounded degraded payloads at the ARC MCP client boundary.

## Implementation approach

1. Add a small transport-error classifier in `sidequest_mcp_client/mcp_brain_client.py`.
   - Match JSON-RPC `error.message` and raised exception strings.
   - Recognize `DAEMON_HTTP_ERROR`, `DAEMON_OFFLINE`, `timed out`, connection refused, connection reset, and HTTP bridge timeout phrases.
   - Return normalized error metadata:
     - `status: "degraded"`
     - `error_code: "daemon_http_timeout"` or nearest specific code
     - `memory_degraded: true`
     - `memory_degraded_reason`
     - `mcp_transport: "http_bridge"` when inferable

2. Wrap non-critical read APIs with degraded fallbacks.
   - `current_truth`
   - `recall_lessons`
   - `recall_plans`
   - `analogical_search`
   - `recall_scene_graph_priors`
   - `recall_mechanic_priors`
   - knowledge-gap or prior-recall helpers already routed through the client
   - Each method should return the same top-level shape its callers expect, with empty result collections and degradation metadata.

3. Wrap write/notify APIs as best-effort.
   - `notify_turn`
   - `upsert_lesson`
   - `register_plan`
   - `publish_mechanic_summary`
   - `report_outcome`
   - Return a degraded write status instead of raising when the adapter HTTP leg times out.
   - Preserve any existing background/deferred dispatch behavior.

4. Update runtime failure taxonomy.
   - In `agents/arc3/failure_taxonomy.py`, classify MCP bridge transport errors as `tool_timeout` or `memory_timeout`, not `llm_timeout`.
   - In `agents/arc3/runner.py`, preserve degradation metadata in final task results, progress snapshots, and graph health summaries.

5. Update world-model evaluation diagnostics.
   - In `benchmarks/arc3/world_model_eval.py`, surface:
     - `memory_degraded`
     - `memory_degraded_reason`
     - `mcp_http_timeout_count`
     - `memory_transfer_state="capability_or_transport_degraded"` when prior recall was blocked by bridge timeout.

6. Keep startup readiness distinct from runtime degradation.
   - Readiness checks may still fail when explicitly required by the caller.
   - Once a run has started, optional memory/tool failures should degrade unless the specific command is semantically required to continue.

## Concrete file additions/edits

- `sidequest_mcp_client/mcp_brain_client.py`
  - Add `_classify_mcp_transport_error(...)`.
  - Add `_degraded_read_payload(...)` and `_degraded_write_payload(...)`.
  - Use these helpers in memory read/write wrapper methods.

- `agents/arc3/failure_taxonomy.py`
  - Add or update classification rules for SideQuests bridge errors.

- `agents/arc3/runner.py`
  - Capture memory degradation fields from graph health and MCP client responses.
  - Ensure final failure class is not `llm_timeout` unless an actual LLM call times out.

- `benchmarks/arc3/world_model_eval.py`
  - Include transport-degradation fields in step and summary metrics.

- `tests/test_a091_http_mcp_bridge_degradation.py`
  - Add focused regression tests for JSON-RPC bridge errors, raised timeout exceptions, degraded read fallbacks, degraded write fallbacks, and final classification.

- `tests/test_mcp_brain_client.py`
  - Extend existing MCP contract coverage if the fixture setup is already present there.

## API/interface changes

Degraded read payload example:

```json
{
  "status": "degraded",
  "items": [],
  "memory_degraded": true,
  "memory_degraded_reason": "daemon_http_timeout",
  "error_code": "daemon_http_timeout",
  "mcp_transport": "http_bridge"
}
```

Degraded write payload example:

```json
{
  "status": "degraded",
  "accepted": false,
  "deferred": true,
  "memory_degraded": true,
  "memory_degraded_reason": "daemon_http_timeout",
  "error_code": "daemon_http_timeout",
  "mcp_transport": "http_bridge"
}
```

Final result/telemetry fields:

```json
{
  "memory_degraded": true,
  "memory_degraded_reason": "daemon_http_timeout",
  "mcp_http_timeout_count": 1,
  "failure_class": "tool_timeout"
}
```

## Tests to add or run

```bash
pytest -q tests/test_a091_http_mcp_bridge_degradation.py
pytest -q tests/test_mcp_brain_client.py
pytest -q tests/test_import_boundary.py
make test-a
```

Manual validation:

```bash
export SIDEQUESTS_BRAIN_URL="http://127.0.0.1:7799/mcp"
python run_single_puzzle.py \
  --live-smoke \
  --num-puzzles 1 \
  --max-steps 30 \
  --world-model-eval \
  --model deepseek-r1:8b \
  --base-url http://localhost:11434/v1 \
  --timeout-seconds 900 \
  --max-retries 5
```

Expected manual result:

- The run may mark memory as degraded if the bridge times out.
- It must not abort before step 1 solely because of `DAEMON_HTTP_ERROR`.
- If the model itself later times out, that should be separately classified as an LLM timeout.

## Assumptions/defaults

- The HTTP bridge is a valid MCP-adjacent simulation path and should remain supported.
- Optional graph memory improves planning but must not be required for ARC action execution.
- Degraded memory must be visible in telemetry so benchmark analysis does not confuse “no useful priors” with “memory transport unavailable.”
- Do not import SideQuests internals into ARC runtime code.
