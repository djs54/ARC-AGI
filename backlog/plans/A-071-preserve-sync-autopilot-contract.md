# Plan: A-071 — preserve sync autopilot contract around async matcher

## Card metadata

- **Card:** A071
- **Priority:** P1
- **Layer:** ARC runtime
- **Depends on:** A050, A065

## Summary

Split synchronous autopilot decision logic from async graph/matcher enrichment. Keep `_try_autopilot` synchronous for compatibility, and introduce an awaited helper for call sites that can safely perform async memory/matcher work.

## Implementation approach

1. Inspect all `_try_autopilot` call sites and tests.
2. Restore `_try_autopilot` as a regular `def`.
3. Move awaited operations to a helper such as `_try_autopilot_async_enriched`.
4. Ensure async runtime paths explicitly call and await the helper.
5. Keep fallback behavior identical when async enrichment is unavailable.
6. Add trace metadata:
   - `autopilot_mode=sync`
   - `autopilot_mode=async_enriched`
   - `autopilot_enrichment_status`

## Concrete file additions/edits

- `agents/arc3/orchestrator.py`
  - Restore sync `_try_autopilot`.
  - Add async helper if needed.
  - Update awaited runtime call site.
- `tests/test_b166_deterministic_autopilot.py`
  - Assert direct sync call returns action/None.
- `tests/test_b175_autopilot_wall_detection.py`
  - Preserve wall-hit tests.
- `tests/test_arc3_orchestrator.py`
  - Cover async-enriched path if existing fixtures allow it.

## API/interface changes

Internal method contract:

- `_try_autopilot(...)` remains sync.
- New async helper may be added, but only awaited call sites should use it.

## Tests to add or run

Validation commands:

```bash
pytest -q tests/test_b166_deterministic_autopilot.py tests/test_b175_autopilot_wall_detection.py
pytest -q tests/test_arc3_orchestrator.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Synchronous autopilot should prefer local deterministic observations over fresh memory calls.
- Async enrichment is optional; failure to enrich must degrade to the sync decision path.
