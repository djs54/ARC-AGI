# Plan: A-048 — re-fix orchestration status false positives

## Card metadata

- **Card:** A048
- **Priority:** P2
- **Layer:** evaluation/harness
- **Depends on:** A042, A047

## Summary

Reproduce and resolve regressed `orchestration_status=violation` false positives in constrained live-smoke contexts.

## Implementation approach

1. Build/identify regression fixture from recent smoke output.
2. Compare constrained-context logic to A042 expected behavior.
3. Patch evaluation heuristics to avoid false violation labels when constraints limit diversity.
4. Preserve true-violation detection rules and add explicit tests for both branches.

## Concrete file edits

- `benchmarks/arc3/trajectory_eval.py`
- `tests/test_b182_enhanced_metrics.py`
- `tests/test_benchmark_report.py`

## API / interface changes

- No public API changes.

## Tests to run

- `pytest -q tests/test_b182_enhanced_metrics.py tests/test_benchmark_report.py`

## Validation commands

- `rg -n "orchestration_status|violation|action_diversity|available_actions" submission_results_single.live.jsonl`

## Assumptions / defaults

- Single-puzzle and constrained-action runs should not be penalized for unavailable choices.
