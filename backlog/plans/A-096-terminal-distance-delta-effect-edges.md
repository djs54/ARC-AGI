# Plan: A-096 — Terminal-distance delta on effect edges

## Card metadata

- **Card:** A096
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A092, A094, A100

## Summary

Preserve terminal-distance deltas as first-class graph evidence. This keeps non-terminal movement available for route planning without pretending it is terminal success.

## Implementation approach

1. Extend `WorldModelCompiler` to compare the previous and current terminal goal distance.
2. Store `goal_distance_before`, `goal_distance_after`, `goal_distance_delta`, and `distance_trend` on action-effect claims.
3. Persist those fields on `Effect` nodes in `WorldModelGraph.apply_compiled_delta`.
4. Surface the fields in `WorldModelEvaluator` step rows.
5. Keep unknown-distance behavior conservative.

## Concrete file additions/edits

- `agents/arc3/world_model_compiler.py`
- `agents/arc3/world_model.py`
- `agents/arc3/orchestrator.py`
- `benchmarks/arc3/world_model_eval.py`
- `tests/test_a096_terminal_distance_delta_effect_edges.py`

## API/interface changes

Compiled action-effect claim props add:

```json
{
  "goal_distance_before": 42.5,
  "goal_distance_after": 34.0,
  "goal_distance_delta": -8.5,
  "distance_trend": "improving"
}
```

## Tests to add or run

```bash
.venv/bin/python -m pytest -q tests/test_a096_terminal_distance_delta_effect_edges.py
.venv/bin/python -m pytest -q tests/test_a092_terminal_aligned_meaningful_progress.py tests/test_a078_world_model_evaluation_harness.py
make test-a
```

## Assumptions/defaults

- Negative distance delta means closer to the inferred terminal goal.
- Unknown distance does not count as improving or regressing.
- This card records evidence only; route policy changes happen in A097-A099.
