# Plan: A-109 — Pattern correspondence goal planner

## Card metadata

- **Card:** A109
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A101, A102, A103, A107, A108

## Summary

Add graph traversals that turn pattern/copy/color-correspondence hypotheses into ranked coordinate experiments.

## Implementation approach

1. Extend mechanic graph summaries with panel-like groupings when possible:
   - group nearby repeated colored blocks into `PatternPanel` summaries.
   - assign stable `panel_id`, bbox, centroid, object signatures.
2. Add world-model traversal helpers:
   - `find_pattern_correspondence_candidates(goal_type, limit)`
   - `find_panel_mismatches(limit)`
   - `get_click_candidate_evidence(candidate_id)`
3. Ranking rules for `color_correspondence`:
   - Prefer candidates in the target/framed/current panel.
   - Prefer candidates whose color/object relation differs from matching panels.
   - Prefer centers of framed/gray/white motifs.
   - Penalize candidates already falsified.
4. Prediction generation:
   - `configuration_change` when candidate is a high-confidence mismatch or target center.
   - `color_match_confirmed` when candidate tests matching color relation.
   - `mismatch_resolved` when candidate is the only object differing from source panels.
   - `level_advance` only when level/terminal evidence has previously followed this relation.
5. Bounded traversal:
   - max panels: 8
   - max candidates returned: 16
   - max evidence path ids: 8

## Concrete file additions/edits

- Edit `agents/arc3/world_model_planner.py`
- Edit `agents/arc3/world_model.py`
- Edit `agents/arc3/click_candidates.py`
- Edit `agents/arc3/goal_hypothesis.py`
- Edit `benchmarks/arc3/world_model_eval.py`
- Add `tests/test_a109_pattern_correspondence_goal_planner.py`

## API/interface changes

Planner candidate prediction shape:

```python
{
    "effect_class": "configuration_change",
    "goal_type": "color_correspondence",
    "falsification_condition": "click produces no frame/configuration delta",
    "evidence_path": ["goal-...", "panel-...", "rel-...", "click-..."],
    "confidence": 0.0,
}
```

World-model query helpers should return plain dicts to avoid coupling tests to internal node classes.

## Tests to add or run

```bash
.venv/bin/python -m pytest -q tests/test_a109_pattern_correspondence_goal_planner.py
.venv/bin/python -m pytest -q tests/test_a107_graph_click_candidate_generator.py tests/test_a108_coordinate_aware_cheap_probe_planner.py tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Use labeled-property-graph style edges and edge evidence; no RDF/ontology layer is needed.
- Prefer explainable bounded traversals over global image analysis.
- If no panel structure can be extracted, fall back to A107 candidate rank without fabricating panel evidence.
