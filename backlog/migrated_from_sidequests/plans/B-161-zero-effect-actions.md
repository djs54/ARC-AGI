# Superseded Plan for B161 — Zero-Effect Diagnosis

> This draft is retained only for historical context.
> The diagnosis was corrected after live trace review.
>
> **Active plan:** `backlog/plans/B-161-goal-directed-navigation.md`

## Corrected Diagnosis

The latest evidence shows the agent is **not** stuck because all actions are zero-effect.
Instead, it **does move the player**, but it fails to navigate toward the goal reliably.

Verified evidence from the latest live smoke `live_qwen25_7b_smoke_1775330224`:
- `submission_results_single.json` reports `no_progress_step_count: 15`
- `tests/test_arc3_orchestrator.py` verifies this counter increments on **zero reward**, not on zero pixel deltas
- Multiple steps show non-zero `pixels_changed`, including meaningful movement / interaction effects:
  - `ACTION3(left) -> 48`
  - `ACTION4(right) -> 72`
  - `ACTION5(interact) -> 72-96`
  - `ACTION1(up) -> 48-49` on some steps

## What Changed

The earlier "zero-effect actions" theory has been replaced by a better one:

1. The game is a **maze-navigation puzzle**
2. The agent already knows the direction mappings
3. The missing piece is **spatial / goal-directed reasoning** in the prompt and solver state
4. The next implementation focus should be:
   - player / goal position tracking
   - directional guidance in prompts
   - movement-history summaries
   - better interpretation of `ACTION5` effects

## Recommended Next Order

1. `B161` — goal-directed navigation
2. `B162` — front-load grid analysis
3. `B163` — faster archetype classification
4. `B164` — better small-model prompts
5. `B165` — persist lessons across runs
