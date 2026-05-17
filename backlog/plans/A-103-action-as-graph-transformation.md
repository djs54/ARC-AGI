# Plan: A-103 — Action as graph transformation

## Card metadata

- **Card:** A103
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A074, A086, A101, A102

## Summary

Learn action semantics by diffing mechanic graph snapshots and storing action effects as graph transformations with configuration ids.

## Implementation approach

1. Add `agents/arc3/graph_transform.py` with:
   - `GraphTransformation`: `action_id`, `step`, `transform_class`, `confidence`, `before_config_hash`, `after_config_hash`, `affected_object_ids`, `changed_relation_ids`, `goal_relevance`, `evidence_path_ids`, `props`.
   - `GraphTransformationLearner.diff(before_snapshot, after_snapshot, action, active_goal_hypotheses)`.
2. Detect bounded transformation classes:
   - `configuration_cycle_step`
   - `rotation_or_permutation`
   - `spoke_endpoint_swap`
   - `link_rewire`
   - `hub_phase_change`
   - `goal_alignment_change`
   - `irrelevant_visual_churn`
3. Persist transformations into `WorldModelGraph`:
   - `(:Action)-[:TRANSFORMED]->(:Configuration)`
   - `(:Configuration)-[:HAS_TRANSFORM {action_id}]->(:Configuration)`
   - `(:GraphTransformation)-[:SUPPORTS|CONTRADICTS]->(:GoalHypothesis)`
4. Update `WorldModelCompiler` so `reversible_movement` can be upgraded to `configuration_cycle_step` when graph transformation evidence exists.
5. Update `WorldModelPlanner` to consume transformation predictions before generic action-effect predictions.
6. Add JSONL metrics:
   - `graph_transform_class`
   - `configuration_hash`
   - `goal_relevance`
   - `affected_object_count`

## Concrete file additions/edits

- Add `agents/arc3/graph_transform.py`
- Edit `agents/arc3/world_model.py`
- Edit `agents/arc3/world_model_compiler.py`
- Edit `agents/arc3/world_model_planner.py`
- Edit `benchmarks/arc3/world_model_eval.py`
- Add `tests/test_a103_action_as_graph_transformation.py`

## API/interface changes

`WorldModelGraph` should add:

```python
def record_graph_transformation(self, transformation: GraphTransformation) -> str: ...
def get_recent_graph_transformations(self, action_id: str | None = None, limit: int = 8) -> list[dict]: ...
```

## Tests to add or run

```bash
.venv/bin/python -m pytest -q tests/test_a103_action_as_graph_transformation.py tests/test_a086_evidence_backed_planner_predictions.py
.venv/bin/python -m pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Transformation matching should use stable object signatures from A102.
- Bound relation diffs by top changed objects/edges.
- Do not promote every pixel change to graph transformation; require object/relation-level evidence.
