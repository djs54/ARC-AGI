# Plan: A-043 — fix static pattern-match progress

## Card metadata

- **Card:** A043
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A041

## Summary

Stop `pattern_match_progress` from reporting static high similarity/trend values that prematurely force finish mode.

## Implementation approach

1. Trace `pattern_match_progress` computation inputs and defaults.
2. Identify where `0.9 stable` is introduced (seed/default/cache/normalization).
3. Correct similarity/trend computation to reflect actual per-step deltas.
4. Tighten finish-phase gating so early static confidence cannot trigger autopilot lock.
5. Add regression tests reproducing prior static-confidence behavior.

## Concrete file edits

- `agents/arc3/solver.py`
- `agents/arc3/orchestrator.py`
- `agents/arc3/runner.py` (if phase transitions centralized)
- `tests/test_arc3_solver.py`
- `tests/test_arc3_orchestrator.py`

## API / interface changes

- No public API changes.
- Internal progress metadata may include clearer derivation fields for diagnostics.

## Tests to run

- `pytest -q tests/test_arc3_solver.py tests/test_arc3_orchestrator.py`
- `python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 50 --card-id pattern_progress_verify`

## Validation commands

- `rg -n "pattern_match_progress|phase=finish|decision_source=autopilot" agent_execution_trace.json submission_results_single.live.jsonl`

## Assumptions / defaults

- Finish mode should require sustained evidence from real trajectory progress, not static defaults.
