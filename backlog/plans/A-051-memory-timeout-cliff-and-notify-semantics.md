# Plan: A-051 — memory timeout cliff and notify semantics

## Card metadata

- **Card:** A051
- **Priority:** P1
- **Layer:** transport/client seam
- **Depends on:** A044, A050

## Summary

Eliminate ~10s memory timeout cliff and mixed `notify_turn` ingestion semantics.

## Implementation approach

1. Audit timeout defaults and retry/fallback branches for notify/upsert.
2. Choose one mode for smoke runtime:
   - strict async non-blocking, or
   - strict sync with explicit longer cap.
3. Remove mixed branch behavior producing both `ingested` and `queued_async` unpredictably.
4. Add explicit mode telemetry field per notify event.

## Concrete file edits

- `sidequest_mcp_client/mcp_brain_client.py`
- `sidequest_mcp_client/mcp_session.py`
- `agents/arc3/runner.py`
- `tests/test_a044_non_blocking_notify.py`
- `tests/test_mcp_brain_client.py`

## Tests to run

- `pytest -q tests/test_a044_non_blocking_notify.py tests/test_mcp_brain_client.py`
- `python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 30 --card-id a051_notify_timeout_regression`
