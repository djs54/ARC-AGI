# Plan: A-106 — Coordinate-aware click action identity

## Card metadata

- **Card:** A106
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A040, A062, A073, A101, A102

## Summary

Make click coordinates part of runtime action identity so click-only games are represented as many coordinate experiments, not one repeated `ACTION6` action.

## Implementation approach

1. Add a small helper in `agents/arc3/orchestrator.py` or `agents/arc3/world_model.py`:

   ```python
   def build_action_identity(action_id: str, x: int | None = None, y: int | None = None) -> str:
       if action_id in COORDINATE_REQUIRED_ACTIONS and x is not None and y is not None:
           return f"{action_id}@{int(x)},{int(y)}"
       return action_id
   ```

2. Define coordinate-required action detection:
   - `ACTION6` is coordinate-required when available controls include click or when previous coordinate relevance says coordinates matter.
   - Do not force coordinates for keyboard/directional actions.
3. Before execution, validate coordinate-required actions:
   - If missing `x/y` and a graph click candidate is available, repair the action with candidate coordinates.
   - If missing `x/y` and no candidate exists, mark the action invalid and trigger replan/LLM instead of executing a null click.
4. Store `action_identity` everywhere action repetition matters:
   - step history
   - world-model `Action` nodes
   - fatigue/quarantine keys
   - falsification tracking
   - cheap-probe evidence
5. Preserve back-compat:
   - Existing `action_id` stays as the environment action id.
   - New code should read `action_identity` when it needs experiment identity.

## Concrete file additions/edits

- Edit `agents/arc3/orchestrator.py`
- Edit `agents/arc3/world_model.py`
- Edit `agents/arc3/world_model_compiler.py`
- Edit `agents/arc3/reasoning_controller.py`
- Edit `agents/arc3/world_model_planner.py`
- Edit `benchmarks/arc3/world_model_eval.py`
- Add `tests/test_a106_coordinate_aware_click_action_identity.py`

## API/interface changes

Runtime step/action records add:

```python
{
    "action_id": "ACTION6",
    "action_identity": "ACTION6@24,17",
    "x": 24,
    "y": 17,
    "coordinate_required": True,
    "missing_coordinate_click": False,
}
```

World-model action nodes should persist `action_identity` as the stable id or indexed property while retaining `action_id`.

## Tests to add or run

```bash
.venv/bin/python -m pytest -q tests/test_a106_coordinate_aware_click_action_identity.py
.venv/bin/python -m pytest -q tests/test_a040_goal_conditioned_action6_coordinate_selection.py tests/test_a062_coordinate_relevance.py tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- `ACTION6` is the canonical click action in current ARC-AGI-3 live API traces.
- Null click execution is never useful when the UI exposes click-only control and frame hash does not change.
- Preserve the MCP seam. This card is runtime-local and adds no SideQuests imports or MCP methods.
