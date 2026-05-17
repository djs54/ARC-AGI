# Plan: A-093 — fast prediction falsification and action quarantine

## Card metadata

- **Card:** A093
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A086, A089, A092

## Summary

Prediction falsification should be a first-class graph operation. When an action's predicted effect is contradicted by observed effects, the world model should update belief and alter future action selection immediately.

## Implementation approach

1. Add graph helper for action contradiction state: recent falsification count, latest predicted effect, latest actual effect, quarantine-until step, and evidence path.
2. Lower the high-confidence falsification threshold for progress predictions.
3. Update planner ranking to exclude quarantined actions from exploit candidates while allowing forced coverage probes.
4. Update the reasoning controller to escalate when all exploit candidates are quarantined.
5. Emit telemetry.

## Concrete file additions/edits

- `agents/arc3/world_model.py`
- `agents/arc3/world_model_planner.py`
- `agents/arc3/reasoning_controller.py`
- `agents/arc3/orchestrator.py`
- `tests/test_a093_fast_prediction_falsification_action_quarantine.py`

## API/interface changes

```json
{
  "action_id": "ACTION3",
  "prediction_falsification_count": 2,
  "quarantined_until_step": 35,
  "quarantine_reason": "predicted_object_progress_but_pixel_churn"
}
```

## Tests to add or run

```bash
pytest -q tests/test_a093_fast_prediction_falsification_action_quarantine.py
pytest -q tests/test_a086_evidence_backed_planner_predictions.py tests/test_a089_graph_backed_planner_prediction_edges.py
make test-a
```

## Assumptions/defaults

- Default quarantine TTL: 5 steps.
- High-confidence progress prediction miss threshold: 2 recent misses.
- Forced coverage probes may still test a quarantined action once when evidence is stale.
