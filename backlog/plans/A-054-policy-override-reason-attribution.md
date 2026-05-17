# Plan: A-054 — policy-override reason attribution

## Card metadata

- **Card:** A054
- **Priority:** P2
- **Layer:** ARC runtime
- **Depends on:** A045

## Summary

Make policy overrides auditable by emitting structured override reasons in trace events.

## Implementation approach

1. Add policy id + trigger reason fields on override path.
2. Include before/after action ids and confidence inputs.
3. Update trace schemas/tests to assert fields exist.

## Concrete file edits

- `agents/arc3/orchestrator.py`
- `agents/arc3/solver.py`
- `tests/test_arc3_orchestrator.py`

## Tests to run

- `pytest -q tests/test_arc3_orchestrator.py`
