# Plan: A-105 — Cross-level mechanic transfer from solved relations

## Card metadata

- **Card:** A105
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A075, A081, A101, A102, A103, A104

## Summary

Compile solved-level evidence into compact graph templates and reuse those templates across later levels in the same game and aggregate memory.

## Implementation approach

1. Add `agents/arc3/level_transfer.py` with:
   - `LevelSolutionTemplate`: `id`, `game_id`, `level_index`, `goal_type`, `goal_relation_signature`, `mechanic_signature`, `action_transform_signature`, `confidence`, `evidence_path_ids`, `props`.
   - `LevelTransferCompiler.compile_on_level_advance(...)`.
   - `LevelTransferMatcher.match(current_mechanic_graph, active_goal_hypotheses, templates)`.
2. Detect level advance from existing `env_signals.levels_completed`, terminal state, or score/level fields.
3. Compile the template from:
   - active successful goal hypothesis from A101.
   - mechanic graph signature from A102.
   - action transformation signature from A103.
   - cycle evidence from A104 when applicable.
4. Store templates in `WorldModelGraph`:
   - `(:LevelSolutionTemplate)`
   - `(:Level)-[:SOLVED_BY]->(:LevelSolutionTemplate)`
   - `(:LevelSolutionTemplate)-[:USES_GOAL]->(:GoalHypothesis)`
   - `(:LevelSolutionTemplate)-[:USES_TRANSFORM]->(:GraphTransformation)`
5. Planner integration:
   - match current level graph to latest template before generic route/action candidates.
   - emit `decision_source="level_template"` when selected.
6. MCP seam:
   - add normalized client methods only if missing:
     - `publish_level_solution_template`
     - `recall_level_solution_templates`
   - degrade cleanly as `capability_missing` if sidequests-brain does not yet support them.
7. Eval integration:
   - `level_template_count`
   - `level_template_match_score`
   - `level_template_used`
   - `level_template_id`

## Concrete file additions/edits

- Add `agents/arc3/level_transfer.py`
- Edit `agents/arc3/world_model.py`
- Edit `agents/arc3/world_model_planner.py`
- Edit `agents/arc3/orchestrator.py`
- Edit `sidequest_mcp_client/mcp_brain_client.py`
- Edit `benchmarks/arc3/world_model_eval.py`
- Add `tests/test_a105_cross_level_mechanic_transfer.py`

## API/interface changes

Runtime graph API:

```python
def record_level_solution_template(self, template: LevelSolutionTemplate) -> str: ...
def get_level_solution_templates(self, limit: int = 8) -> list[dict]: ...
```

MCP client API should normalize missing support:

```python
async def publish_level_solution_template(self, template: dict) -> dict: ...
async def recall_level_solution_templates(self, signature: dict, limit: int = 5) -> dict: ...
```

## Tests to add or run

```bash
.venv/bin/python -m pytest -q tests/test_a105_cross_level_mechanic_transfer.py tests/test_a081_aggregate_mechanic_memory_transfer.py tests/test_mcp_brain_client.py
.venv/bin/python -m pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Prefer same-game templates over aggregate memory.
- Publish only compact templates; never publish raw screenshots/full grids/full traces.
- Preserve the MCP seam. Runtime code must not import `mcp_engine.*` or `sidequests.*`.
