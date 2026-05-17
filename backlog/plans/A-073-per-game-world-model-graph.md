# Plan: A-073 — per-game world model graph

## Card metadata

- **Card:** A073
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A065, A068, A072

## Summary

Add a bounded per-game world model graph that turns local ARC observations into explicit causal structure. This is the foundation for the redesign: reason like a strong model, but force beliefs to live in a graph-backed, evidence-auditable representation.

Graph-solution classification: graph is the right fit because the workload is relationship-heavy and traversal-oriented. Use a labeled property graph, not RDF, because ARC runtime needs direct operational traversal with edge properties for confidence, phase, step, and provenance. Do not introduce a database dependency in the hot path.

## Implementation approach

1. Create `agents/arc3/world_model.py`.
2. Define lightweight dataclasses:
   - `WorldNode(id, label, props)`
   - `WorldEdge(src, rel, dst, props)`
   - `WorldModelGraph`
   - `WorldModelSummary`
3. Provide graph mutation helpers:
   - `record_state(...)`
   - `record_action(...)`
   - `record_observation(...)`
   - `record_effect(...)`
   - `link_support(...)`
   - `link_contradiction(...)`
   - `upsert_hypothesis(...)`
   - `upsert_mechanic_candidate(...)`
   - `upsert_goal_model(...)`
4. Provide bounded query helpers:
   - `action_effect_table(limit=...)`
   - `active_hypotheses(limit=...)`
   - `demoted_hypotheses(limit=...)`
   - `mechanic_candidates(limit=...)`
   - `next_experiment_candidates(limit=...)`
   - `to_prompt_summary(max_chars=...)`
   - `to_trace_snapshot()`
5. Use stable IDs derived from task id, session id, step, action id, state hash, and hypothesis/effect fingerprints.
6. Enforce caps:
   - max states retained in prompt summary
   - max observations per action
   - max active hypotheses
   - max demoted hypotheses
   - max serialized snapshot size
7. Wire the graph into orchestrator lifecycle:
   - initialize on task start
   - update after observation/evaluation
   - update on hypothesis changes
   - expose compact summary to solver/replan prompts
8. Keep persistence optional:
   - no blocking MCP write in execute phase
   - compact snapshot can be queued/deferred through `MCPBrainClient` after solve/model/replan boundaries

## Concrete file additions/edits

- `agents/arc3/world_model.py`
  - New graph dataclasses, stable ID helpers, mutation helpers, and bounded query helpers.
- `agents/arc3/orchestrator.py`
  - Own `WorldModelGraph` lifecycle and update it at reasoning/evaluation boundaries.
- `agents/arc3/runner.py`
  - Include `world_model_summary` and `world_model_snapshot` in trace/progress packaging with size caps.
- `agents/arc3/solver.py`
  - Accept optional compact world-model summary in solve/replan context.
- `sidequest_mcp_client/mcp_brain_client.py`
  - Add optional deferred `upsert_world_model_snapshot(...)` seam method if no existing generic write fits.
- `tests/test_a073_per_game_world_model.py`
  - New focused tests.

## API/interface changes

- Add internal runtime object `WorldModelGraph`.
- Add optional trace fields:
  - `world_model_summary`
  - `world_model_snapshot`
  - `world_model_node_count`
  - `world_model_edge_count`
- If a memory write is needed, add a new MCP-client method rather than importing SideQuests internals.

## Starter schema

```text
(:Game {id, task_id, session_id})
(:State {id, hash, step})
(:Action {id, action_id, args_signature})
(:Observation {id, step, frame_hash, reward, terminal_score})
(:Effect {id, kind, magnitude, meaningful})
(:Object {id, signature, role})
(:Hypothesis {id, scope, claim, confidence, status})
(:Mechanic {id, name, confidence})
(:GoalModel {id, kind, confidence})

(:Game)-[:HAS_STATE]->(:State)
(:State)-[:ACTION_TAKEN {step}]->(:Action)
(:Action)-[:CAUSED {confidence, step}]->(:Effect)
(:Effect)-[:OBSERVED_IN]->(:Observation)
(:Observation)-[:SUPPORTS {weight}]->(:Hypothesis)
(:Observation)-[:CONTRADICTS {weight}]->(:Hypothesis)
(:Hypothesis)-[:EXPLAINS]->(:Mechanic)
(:Mechanic)-[:PREDICTS]->(:Effect)
(:Game)-[:CURRENT_GOAL_MODEL]->(:GoalModel)
```

## Tests to add or run

Add tests for:

- stable IDs are deterministic
- action-effect edges are created from step evidence
- support and contradiction edges are traversable
- summaries obey node/edge/character caps
- graph snapshot survives dict/object solve-context packaging
- import boundary remains clean

Validation commands:

```bash
pytest -q tests/test_a073_per_game_world_model.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Use an in-memory LPG-style representation first.
- Avoid engine-specific Cypher/Gremlin dependencies in runtime.
- Persist only compact summaries through the MCP seam until the sidequests-brain graph API is deliberately expanded.
