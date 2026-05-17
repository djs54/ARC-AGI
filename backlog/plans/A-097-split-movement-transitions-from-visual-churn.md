# Plan: A-097 — Split movement transitions from visual churn

## Card metadata

- **Card:** A097
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A096

## Summary

Refine the effect taxonomy so the graph distinguishes movement/state transitions from irrelevant visual churn. This preserves useful exploration facts for later traversal.

## Implementation approach

1. Add a classifier in `WorldModelCompiler` that maps distance deltas and state hash changes into transition effect classes.
2. Emit `distance_improving_move`, `distance_regressing_move`, `reversible_movement`, `state_transition`, or `visual_churn` where possible.
3. Update graph query helpers to treat useful transitions as route evidence, not terminal progress.
4. Keep compatibility mappings for existing controller branches that expect `pixel_churn`.
5. Update planner prediction/eval fields to report refined effect classes.

## Concrete file additions/edits

- `agents/arc3/world_model_compiler.py`
- `agents/arc3/world_model.py`
- `agents/arc3/reasoning_controller.py`
- `agents/arc3/world_model_planner.py`
- `benchmarks/arc3/world_model_eval.py`
- `tests/test_a097_movement_transition_effect_taxonomy.py`

## API/interface changes

Effect node `kind` may now be one of:

```json
[
  "distance_improving_move",
  "distance_regressing_move",
  "reversible_movement",
  "state_transition",
  "visual_churn"
]
```

## Tests to add or run

```bash
.venv/bin/python -m pytest -q tests/test_a097_movement_transition_effect_taxonomy.py
.venv/bin/python -m pytest -q tests/test_a096_terminal_distance_delta_effect_edges.py tests/test_a094_multi_action_churn_exhaustion_decision.py
make test-a
```

## Assumptions/defaults

- Transition classes are not terminal progress by themselves.
- `visual_churn` remains the true no-route, no-progress bucket.
- Existing `pixel_churn` checks should continue to work through a helper mapping during migration.
