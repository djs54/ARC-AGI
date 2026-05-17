# Plan: A-045 — autopilot wall-hit retargeting

## Card metadata

- **Card:** A045
- **Priority:** P1
- **Layer:** ARC runtime
- **Depends on:** A043

## Summary

Prevent autopilot from repeating blocked moves against stale targets by retargeting or disengaging after wall-hit streaks.

## Implementation approach

1. Locate wall-hit event tracking and autopilot target state.
2. Add threshold-based wall-hit streak detector.
3. On threshold trigger:
   - invalidate/recompute target, or
   - disengage autopilot and return control to planner
4. Add short cooldown to avoid immediate re-lock on same blocked trajectory.
5. Emit explicit rationale labels for retarget/disengage paths.

## Concrete file edits

- `agents/arc3/orchestrator.py`
- `agents/arc3/solver.py`
- `tests/test_arc3_orchestrator.py`
- `tests/test_arc3_solver.py`

## API / interface changes

- No public API changes.
- Internal telemetry gains explicit autopilot fallback reason markers.

## Tests to run

- `pytest -q tests/test_arc3_orchestrator.py tests/test_arc3_solver.py`
- `python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 75 --card-id autopilot_wall_verify`

## Validation commands

- `rg -n "autopilot_wall_detected|autopilot\\[finish\\]|retarget|disengage" agent_execution_trace.json submission_results_single.live.jsonl`

## Assumptions / defaults

- Repeated wall hits are strong evidence of stale/invalid targeting.
