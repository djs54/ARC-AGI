# Plan: A-044 — make notify_turn non-blocking

## Card metadata

- **Card:** A044
- **Priority:** P0
- **Layer:** transport/client seam
- **Depends on:** A043

## Summary

Remove `notify_turn` from the synchronous critical path in smoke execution to reclaim wall-clock budget.

## Implementation approach

1. Inventory all `notify_turn` call sites and per-step frequency.
2. Introduce non-blocking dispatch mode (fire-and-forget) with bounded queue/backpressure.
3. Optionally coalesce duplicate notify payloads within a step.
4. Preserve error logging without blocking step execution.
5. Add metrics counters for dropped/coalesced async notify events.

## Concrete file edits

- `sidequest_mcp_client/mcp_brain_client.py`
- `sidequest_mcp_client/mcp_session.py` (if async helper needed)
- `agents/arc3/runner.py`
- `tests/test_mcp_brain_client.py`
- `tests/test_readiness.py` (if behavior affects readiness assumptions)

## API / interface changes

- Internal: optional `notify_turn_async` / non-blocking mode flag.
- No required CLI changes.

## Tests to run

- `pytest -q tests/test_mcp_brain_client.py tests/test_readiness.py`
- `python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 50 --card-id notify_async_verify`

## Validation commands

- `rg -n "notify_turn|queued|ingested|wall_clock|runtime_seconds" agent_execution_trace.json submission_results_single.live.jsonl`

## Assumptions / defaults

- Lossy telemetry is acceptable for repeated intra-step notify variants if core observations are retained.
