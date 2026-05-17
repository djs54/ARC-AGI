# Plan: A-099 — Bounded graph route planner over state transitions

## Card metadata

- **Card:** A099
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A096, A097, A098

## Summary

Use the per-game LPG as an operational route planner for navigation games. The planner should traverse discovered state transitions cheaply and propose bounded action sequences before spending another LLM cycle.

## Implementation approach

1. Add `WorldModelGraph.get_state_transition_graph(...)` and `find_route_candidates(...)` helpers.
2. Represent route edges with action id, effect id, distance delta, state hashes, and confidence.
3. Implement bounded BFS/A*-style candidate generation in `WorldModelPlanner`.
4. Wire `ReasoningController` `route_search_required` decisions to a deterministic action selection path.
5. Emit route candidate telemetry and evidence paths in world-model eval rows.

## Concrete file additions/edits

- `agents/arc3/world_model.py`
- `agents/arc3/world_model_planner.py`
- `agents/arc3/orchestrator.py`
- `agents/arc3/reasoning_controller.py`
- `benchmarks/arc3/world_model_eval.py`
- `tests/test_a099_bounded_graph_route_planner.py`

## API/interface changes

Planner candidate example:

```json
{
  "action_id": "ACTION1",
  "route_actions": ["ACTION1", "ACTION3", "ACTION1"],
  "expected_distance_delta": -8.5,
  "route_confidence": 0.72,
  "evidence_path": ["state-a", "action-a1", "state-b"]
}
```

## Tests to add or run

```bash
.venv/bin/python -m pytest -q tests/test_a099_bounded_graph_route_planner.py
.venv/bin/python -m pytest -q tests/test_a098_race_safe_early_stop_guardrails.py tests/test_a097_movement_transition_effect_taxonomy.py
make test-a
```

## Assumptions/defaults

- Depth defaults to 4, candidate count to 5, and repeated-state expansion to 1 unless config overrides.
- Search must be deterministic for identical graph fixtures.
- Bounded traversal is required; no unbounded graph expansion in the hot path.
