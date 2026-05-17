# Plan: A-040 — goal-conditioned ACTION6 coordinate selection

## Card metadata

- **Card:** A040
- **Priority:** P1
- **Layer:** ARC runtime
- **Depends on:** A023, A025

## Summary

Eliminate ACTION6 center-lock/no-effect behavior by selecting coordinates using current goal signals and anti-repeat logic.

## Implementation approach

1. Identify current ACTION6 target picker and anti-loop logic.
2. Plumb goal-color/archetype hints into ACTION6 selection context.
3. Add candidate ranking:
   - goal-color cells
   - 1-cell frontier around goal-color regions
   - unexplored coverage cells
4. Add short-term coordinate blacklist for recent `no_effect` ACTION6 attempts.
5. Keep fallback deterministic/randomized-with-seed when no signal exists.
6. Add focused tests for coordinate diversity and goal bias.

## Concrete file edits

- `agents/arc3/orchestrator.py`
  - Update ACTION6 target selection path and rationale labels.
- `agents/arc3/solver.py` (if additional goal metadata exposure is required)
- `tests/test_exploration_probing.py`
- `tests/test_arc3_orchestrator.py`

## API / interface changes

- No external API changes.
- Internal rationale strings may include goal-conditioned selection reason.

## Tests to run

- `pytest -q tests/test_exploration_probing.py tests/test_arc3_orchestrator.py`
- `python run_single_puzzle.py --num-puzzles 1 --max-steps 50 --card-id action6_goal_bias_verify`

## Validation commands

- `rg -n "ACTION6|replan_forced_action6_probe|no_effect|coord" agent_execution_trace.json submission_results_single.live.jsonl`

## Assumptions / defaults

- Goal hypotheses are available in solve context during replan/route.
- Maintain existing behavior for non-ACTION6 actions.
