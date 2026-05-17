# Plan: A-047 — restore trajectory eval scoring

## Card metadata

- **Card:** A047
- **Priority:** P1
- **Layer:** evaluation/harness
- **Depends on:** A038, A041

## Summary

Ensure valid live-smoke traces produce trajectory scores instead of `trajectory score not computed`.

## Implementation approach

1. Trace scoring preconditions in `trajectory_eval` and submission packaging.
2. Identify gate(s) that incorrectly classify valid traces as unscorable.
3. Relax/fix gate conditions to require only truly necessary fields.
4. Add fallback extraction from trace artifacts where appropriate.
5. Add regression test with non-zero-step valid trace fixture.

## Concrete file edits

- `benchmarks/arc3/trajectory_eval.py`
- `benchmarks/arc3/submission.py` (if packaging gate participates)
- `tests/test_b186_trajectory_plan_adherence.py`
- `tests/test_benchmark_report.py`

## API / interface changes

- No public API changes.
- Internal scoring gate behavior updated.

## Tests to run

- `pytest -q tests/test_b186_trajectory_plan_adherence.py tests/test_benchmark_report.py`
- `python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 50 --card-id trajectory_score_verify`

## Validation commands

- `rg -n "trajectory score not computed|trajectory\\.total|steps" submission_results_single.live.jsonl submission_results_single.json`

## Assumptions / defaults

- Trace artifacts now carry sufficient step/effect data for scoring after A038/A041.
