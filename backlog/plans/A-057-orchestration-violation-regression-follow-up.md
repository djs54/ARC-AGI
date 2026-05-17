# Plan: A-057 — orchestration violation regression follow-up

## Card metadata

- **Card:** A057
- **Priority:** P3
- **Layer:** evaluation/harness
- **Depends on:** A048, A055

## Summary

Lock down remaining orchestration false-positive regression with constrained-run fixtures.

## Implementation approach

1. Reproduce latest false-positive run shape.
2. Patch constrained-context scoring guards.
3. Add fixture coverage preventing re-regression.

## Concrete file edits

- `benchmarks/arc3/trajectory_eval.py`
- `tests/test_a042_reproduce_false_positive.py`
- `tests/test_b182_enhanced_metrics.py`

## Tests to run

- `pytest -q tests/test_a042_reproduce_false_positive.py tests/test_b182_enhanced_metrics.py`
