# Plan: A-063 — object-centric progress scoring beyond pixel novelty

## Card metadata

- **Card:** A063
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A061, A062

## Summary

Replace raw pixel novelty as the main dense progress proxy with compact object-centric progress components. The smoke shows a structured `9 -> 3` frontier sequence that should be understood as possible path/player expansion, while still remaining separate from terminal success.

Graph-solution classification: this is graph-first inside the agent because scene objects, adjacencies, frontiers, and action outcomes are relationship-heavy. Use a labeled property graph representation for local scene/evidence summaries, then optionally persist compact evidence through SideQuest memory at safe boundaries.

## Implementation approach

1. Build object-delta extraction:
   - connected components by color
   - component size deltas
   - centroid/frontier movement
   - adjacency changes between role-colored objects
   - path-color consumption/replacement
2. Add object progress components:
   - `player_region_expansion`
   - `path_frontier_advance`
   - `distance_to_goal_delta`
   - `goal_region_approach`
   - `meaningless_toggle_penalty`
   - `repeated_location_penalty`
3. Integrate into existing terminal/dense scoring:
   - keep `env_reward` authoritative
   - keep `terminal_value_score` conservative
   - add `object_progress_score` as a policy/trace signal
4. Expose compact summaries to prompt/trace:
   - no full grids
   - include top changed components and role/color ids
5. Use with A061:
   - macro can continue if object progress remains positive
   - macro stops or asks for reasoning if object progress goes flat
6. Produce graph-ready scene/evidence summaries:
   - stable WL/scene hash
   - changed object/component ids
   - changed edges or adjacencies
   - frontier direction and length
   - object-progress components
   - bounded summary for memory writes

## Concrete file additions/edits

- `agents/arc3/grid_analysis.py`
  - Add connected-component delta and frontier summaries.
- `agents/arc3/scene_graph.py`
  - Add object/edge delta helpers if existing scene graph is the cleaner location.
- `agents/arc3/solver.py`
  - Consume object progress in strategy confidence and progress evidence.
- `agents/arc3/orchestrator.py`
  - Add progress-log fields and prompt summaries.
- `benchmarks/arc3/trajectory_eval.py`
  - Preserve object-progress fields in trajectory scoring/reporting.
- `tests/test_a063_object_progress_scoring.py`
  - Fixtures for frontier expansion, random toggles, goal approach, and no-op.

## API/interface changes

- No external API changes.
- Add internal progress fields:
  - `object_progress_score`
  - `object_progress_components`
  - `object_progress_summary`

## Graph-memory model notes

Recommended model: local labeled property graph for scene summaries; persisted memory graph through MCP only after summary compaction.

Starter local schema:

```text
(:Scene {scene_hash, task_id, step})
(:Object {object_id, color, role, size, centroid})
(:ActionEffect {action_id, step, object_progress_score})

(:Scene)-[:CONTAINS]->(:Object)
(:Object)-[:ADJACENT_TO {direction}]->(:Object)
(:ActionEffect)-[:CHANGED]->(:Object)
(:ActionEffect)-[:ADVANCED_FRONTIER]->(:Object)
```

Traversal rule: bound any similarity/provenance lookup by scene hash/archetype/action id and a small hop limit. Do not traverse from high-degree color-only nodes without early filters.

## Tests to add or run

Add tests for:

- connected `9 -> 3` frontier expansion scores positive
- isolated one-pixel toggle scores near zero or negative
- moving closer to a goal-colored component scores positive
- moving away from goal scores lower
- macro continuation uses object progress, not pixel novelty alone

Validation commands:

```bash
pytest -q tests/test_a063_object_progress_scoring.py tests/test_scene_graph.py
pytest -q tests/test_a061_single_action_macro_executor.py tests/test_a062_coordinate_relevance.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Object progress is advisory. It should not override the environment terminal state.
- If role mapping is uncertain, use color/component progress with lower confidence instead of fabricating roles.
