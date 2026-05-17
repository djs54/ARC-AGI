# Plan: A-049 — improve ACTION6 effectiveness in mixed runs

## Card metadata

- **Card:** A049
- **Priority:** P2
- **Layer:** ARC runtime
- **Depends on:** A040, A043

## Summary

Reduce ACTION6 `no_effect` outcomes in mixed-action runs by refining candidate scoring and anti-repeat behavior.

## Implementation approach

1. Profile ACTION6 call outcomes in mixed-action traces.
2. Penalize recently ineffective coordinates and promote frontier/goal-adjacent alternatives.
3. Integrate recent effect class into ACTION6 value updates.
4. Add tests capturing mixed-action (not ACTION6-only) behavior.

## Concrete file edits

- `agents/arc3/orchestrator.py`
- `agents/arc3/solver.py`
- `tests/test_arc3_orchestrator.py`
- `tests/test_exploration_probing.py`

## API / interface changes

- No public API changes.

## Tests to run

- `pytest -q tests/test_arc3_orchestrator.py tests/test_exploration_probing.py`
- `python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 75 --card-id action6_mixed_verify`

## Validation commands

- `rg -n "ACTION6|no_effect|local_change|complex|coord" agent_execution_trace.json submission_results_single.live.jsonl`

## Assumptions / defaults

- ACTION6 remains a secondary action in mixed runs but should provide non-trivial value when chosen.
