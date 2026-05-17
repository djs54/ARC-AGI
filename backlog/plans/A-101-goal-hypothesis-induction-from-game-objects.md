# Plan: A-101 — Goal hypothesis induction from game objects

## Card metadata

- **Card:** A101
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A073, A074, A092, A100

## Summary

Introduce a deterministic goal-hypothesis layer that turns object correspondences into explicit, graph-backed hypotheses before the planner evaluates actions.

## Implementation approach

1. Create `agents/arc3/goal_hypothesis.py` with:
   - `GoalHypothesis` dataclass: `id`, `goal_type`, `claim`, `confidence`, `status`, `evidence_path_ids`, `target_object_ids`, `properties`.
   - `GoalHypothesisInducer` class with `induce(objects, world_model, terminal_context) -> list[GoalHypothesis]`.
2. Implement bounded detectors:
   - `color_correspondence`: same-colored hub/token/ring/portal objects.
   - `reach_target`: player-like object and goal-like object.
   - `collect_or_activate`: object connected to endpoint/marker.
   - `level_advance`: hypotheses supported by `levels_completed` changes.
3. Store hypotheses in `WorldModelGraph` using existing `Hypothesis` nodes plus goal-specific props:
   - `scope="goal_model"`
   - `goal_type`
   - `target_object_ids`
   - `evidence_path_ids`
4. Wire `ARCOrchestrator` to run induction after scene/object extraction and after each terminal/level change.
5. Add compact active-goal summary into the solve context/prompt, capped to top 3 hypotheses.
6. Extend `benchmarks/arc3/world_model_eval.py` with scalar fields:
   - `active_goal_hypothesis_id`
   - `active_goal_type`
   - `active_goal_confidence`
   - `active_goal_evidence_count`

## Concrete file additions/edits

- Add `agents/arc3/goal_hypothesis.py`
- Edit `agents/arc3/world_model.py`
- Edit `agents/arc3/orchestrator.py`
- Edit `benchmarks/arc3/world_model_eval.py`
- Add `tests/test_a101_goal_hypothesis_induction.py`

## API/interface changes

`WorldModelGraph` should add:

```python
def upsert_goal_hypothesis(self, hypothesis: GoalHypothesis) -> str: ...
def get_active_goal_hypotheses(self, limit: int = 3) -> list[dict]: ...
```

World-model step rows should add compact goal fields only, not full hypothesis dumps.

## Tests to add or run

```bash
.venv/bin/python -m pytest -q tests/test_a101_goal_hypothesis_induction.py tests/test_a100_world_model_eval_stream_parity.py
.venv/bin/python -m pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Use a labeled property graph model, not RDF; edge provenance and fast bounded traversal matter more than ontology interoperability.
- Keep all queries bounded by object count and top-k hypotheses.
- Do not call SideQuests directly; any future aggregate goal-memory lookup must go through existing MCP client seams.
