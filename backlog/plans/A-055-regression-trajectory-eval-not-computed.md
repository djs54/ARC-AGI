# Plan: A-055 — regression trajectory eval not computed

## Card metadata

- **Card:** A055
- **Priority:** P1
- **Layer:** evaluation/harness
- **Depends on:** A047, A050

## Summary

Fix residual trajectory-score gating regression for valid live-smoke traces.

## Implementation approach

1. Re-run regression fixture and capture score-skip reason.
2. Patch gating logic to accept valid step/effect traces.
3. Add explicit skip-reason enum for truly invalid cases.

## Concrete file edits

- `benchmarks/arc3/trajectory_eval.py`
- `benchmarks/arc3/submission.py`
- `tests/test_a047_trajectory_scoring.py`
- `tests/test_benchmark_report.py`

## Tests to run

- `pytest -q tests/test_a047_trajectory_scoring.py tests/test_benchmark_report.py`
