# Plan: A-042 — fix single-puzzle orchestration-status false positive

## Card metadata

- **Card:** A042
- **Priority:** P1
- **Layer:** evaluation/harness
- **Depends on:** A038, A041

## Summary

Fix regressed `orchestration_status=violation` false positives for single-puzzle and constrained-action runs while preserving true-failure detection.

## Implementation approach

1. Reproduce with a fixture where behavior is valid but scorecard marks `violation`.
2. Inspect rule inputs (action diversity, available action count, step count, phase violations).
3. Gate/normalize rule for constrained contexts:
   - single-action available set
   - single-puzzle sample size
4. Confirm true violations still trip under unconstrained cases.
5. Document scoring behavior for constrained environments.

## Concrete file edits

- `benchmarks/arc3/trajectory_eval.py`
- `benchmarks/arc3/submission.py` (if packaging of orchestration status needs alignment)
- `tests/test_b182_enhanced_metrics.py`
- `tests/test_benchmark_report.py`

## API / interface changes

- No public API changes.
- Internal scoring thresholds/guards updated for constrained scenarios.

## Tests to run

- `pytest -q tests/test_b182_enhanced_metrics.py tests/test_benchmark_report.py`
- `python run_single_puzzle.py --num-puzzles 1 --max-steps 50 --card-id orch_status_verify`

## Validation commands

- `rg -n "orchestration_status|violation|action_diversity|available_actions" submission_results_single.live.jsonl agent_execution_trace.json`

## Assumptions / defaults

- Scorecard should reflect solver controllables, not penalize immutable environment constraints.
