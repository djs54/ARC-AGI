# Plan: A-052 — regression entity identification unknown

## Card metadata

- **Card:** A052
- **Priority:** P1
- **Layer:** ARC runtime
- **Depends on:** A041, A050

## Summary

Restore entity-id propagation into action-effect writes for puzzle classes regressed to `entity=unknown`.

## Implementation approach

1. Trace entity extraction output in perception.
2. Compare payload passed into effect writer with extraction output.
3. Patch handoff/serialization path dropping entity ids.
4. Add regression fixture from recent smoke event shape.

## Concrete file edits

- `agents/arc3/solver.py`
- `agents/arc3/runner.py`
- `tests/test_arc3_solver.py`
- `tests/test_a041_delta_reconciliation.py` (or dedicated test)

## Tests to run

- `pytest -q tests/test_arc3_solver.py`
