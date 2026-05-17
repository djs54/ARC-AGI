# Plan: A-056 — timeout label regression wall-clock

## Card metadata

- **Card:** A056
- **Priority:** P2
- **Layer:** evaluation/harness
- **Depends on:** A046, A051

## Summary

Reinforce timeout label precedence so wall-clock exhaustion is not mislabeled as `llm_timeout`.

## Implementation approach

1. Map timeout exit points to taxonomy classes.
2. Enforce priority ordering favoring wall-clock classification when applicable.
3. Add regression tests for near-budget memory-heavy run shapes.

## Concrete file edits

- `agents/arc3/failure_taxonomy.py`
- `agents/arc3/runner.py`
- `tests/test_b185_failure_taxonomy.py`

## Tests to run

- `pytest -q tests/test_b185_failure_taxonomy.py`
