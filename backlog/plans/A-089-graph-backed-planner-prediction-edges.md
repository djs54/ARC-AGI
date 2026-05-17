# Plan: A-089 — graph-backed planner prediction edges

## Card metadata

- **Card:** A089
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A073, A074, A086

## Summary

Convert per-game labeled-property-graph action/effect evidence into planner predictions. This is a graph fit because predictions should be derived from local causal neighborhoods: `Action -> Effect -> Observation`, plus contradiction/demotion edges where present.

## Implementation approach

1. Add a bounded world-model query that returns recent causal evidence for an action:
   - action id
   - effect class histogram
   - meaningful-progress rate
   - last supporting observation ids
   - contradiction count
2. Update `WorldModelPlanner` candidate generation so known actions receive:
   - `predicted_observation`
   - `prediction_confidence`
   - `evidence_path`
3. Keep predictions operational:
   - expected effect class
   - expected object/terminal progress boolean
   - expected no-op/churn/cycle class when applicable
4. Preserve graph bounds:
   - cap evidence paths to 5 ids
   - avoid traversing the full graph
5. Feed selected prediction fields into the existing world-model eval stream.

## Concrete file additions/edits

- `agents/arc3/world_model.py`
  - Add `get_action_prediction_evidence(action_id, limit=5)`.
- `agents/arc3/world_model_planner.py`
  - Build `predicted_observation` from evidence.
  - Prefer predicted-and-falsifiable candidates over generic candidates when expected gain is comparable.
- `agents/arc3/orchestrator.py`
  - Preserve selected prediction metadata in solve context and step telemetry.
- `benchmarks/arc3/world_model_eval.py`
  - Ensure prediction fields map to `selected_candidate_has_prediction`.
- `tests/test_a089_graph_backed_planner_prediction_edges.py`
  - Add focused graph fixture tests.

## API/interface changes

Candidate example:

```json
{
  "action_id": "ACTION2",
  "predicted_observation": {
    "effect_class": "object_progress",
    "meaningful_progress": true,
    "confidence": 0.72
  },
  "evidence_path": ["action-task-3-ACTION2", "effect-17", "obs-task-17"]
}
```

## Tests to add or run

```bash
pytest -q tests/test_a089_graph_backed_planner_prediction_edges.py
pytest -q tests/test_a086_evidence_backed_planner_predictions.py tests/test_a078_world_model_evaluation_harness.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Use labeled-property-graph local traversal, not global graph analytics.
- If an action has contradictory evidence, emit a lower-confidence prediction rather than hiding the contradiction.
- Do not add MCP calls in the execute hot path.
