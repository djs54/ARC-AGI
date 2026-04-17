# Plan for B139 — Break Low-Value Explore Loops in `space` Puzzles

## Objective

Turn the remaining live-smoke failure into a solver-quality task with clear, testable evidence:
- the stack now runs cleanly,
- but the solver still ends `NOT_FINISHED` after 15 steps,
- even after inferring `space` + `reach_goal` with high-confidence `player` and `goal` roles.

## Verified Baseline

Latest live smoke (`live_smoke_1775194418`) showed:
- `Correct: False`
- `Steps: 15`
- `final_state: NOT_FINISHED`
- `no_progress_step_count = 15`
- final chunk stayed in `Explore: try unexplored action to gather more information`
- low-value decay across `ACTION3`, `ACTION4`, `ACTION6`, and `ACTION7`

## Hypothesis

The solver is over-penalizing the transition from exploration into directional play. Once `space` + `reach_goal` + player/goal geometry are inferred, the planner should stop treating all remaining moves as generic exploration and instead bias toward actions that look like movement toward the goal.

## Implementation Sketch

1. **Write a failing regression first**
   - Create a focused test where:
     - archetype = `space`
     - victory = `reach_goal`
     - player/goal positions are known with high confidence
     - exploration coverage is complete
     - recent actions are all low-value / zero reward
   - Expected behavior: solver should promote into a directional/goal-driven chunk instead of staying in the generic explore chunk.

2. **Adjust solve policy**
   - Update `PlanChunker` / solve ranking so that when geometry confidence is high, low-value exploration churn no longer dominates.
   - Introduce a geometry-aware bias or scoring feature that favors actions consistent with reducing player→goal distance.

3. **Improve auditability**
   - Add trace fields that make the promotion visible:
     - `geometry_bias`
     - `goal_distance_delta`
     - `explore_to_directional_promotion`

4. **Verify end-to-end**
   - Run the targeted regression tests
   - Run the relevant ARC suites
   - Run one fresh live smoke and compare the resulting action pattern against the current baseline

## Suggested Test Commands

```bash
.venv/bin/python -m pytest tests/test_arc3_solver.py -q
.venv/bin/python -m pytest tests/test_b139_space_goal_policy.py -q
.venv/bin/python run_single_puzzle.py --real-api --num-puzzles 1 --card-id "$LIVE_ID"
```

## Done When

- the new regression passes,
- the solver exits the generic explore loop once geometry is known,
- and the live trace shows actual goal-directed behavior instead of 15 straight no-progress steps.