# B-211 — Structured ActionEffect Writes: Implementation Plan

- **Card:** backlog/B211.md
- **Priority:** P1
- **Dependencies:** B213 (clean codebase), B202 (perceive_step_response exists)

## Summary

Add a `_write_action_effect_record()` helper to `ARCOrchestrator` and call it from `perceive_step_response()` after the existing `notify_turn`. Uses `upsert_lesson` to write a typed, queryable record to SideQuests. No KuzuDB schema changes — `ActionEffect` lesson storage goes through the existing lesson MCP tool.

## Technical Approach

### New helper method: `_write_action_effect_record()`

**File:** `agents/arc3/orchestrator.py`, insert after `_write_action_effect_record` (new method, place near other `_write_*` helpers, around line 1370).

```python
async def _write_action_effect_record(
    self,
    observation: ARC3Observation,
    action_id: str | None,
    reward: float,
    step: int,
    delta_summary: dict,
) -> None:
    """Write a typed ActionEffect lesson record to SideQuests (B211).

    Structured fields allow graph_hypothesize() (B212) to query by
    entity_type + action + effect_class without free-text retrieval.
    """
    if step <= 0 or not action_id:
        return

    # Derive effect_class from FrameDelta fields
    n_changed = int(delta_summary.get("n_cells_changed") or 0)
    apparent_effect = str(delta_summary.get("apparent_effect") or "").lower()
    direction = delta_summary.get("direction")

    if n_changed == 0:
        effect_class = "no_effect"
    elif direction and n_changed <= 4:
        effect_class = "directional_movement"
    elif n_changed > 30:
        effect_class = "large_transformation"
    elif "no_effect" in apparent_effect or "no change" in apparent_effect:
        effect_class = "no_effect"
    else:
        effect_class = "local_change"

    # Pull entity type from existing role map if available
    solve_ctx = self._solve_context or {}
    roles = solve_ctx.get("roles") or {}
    entity_type = "unknown"
    spatial_role = "unknown"
    if roles:
        # Check player position entity type
        player_pos = self._player_position
        if player_pos:
            for role_key, role_data in roles.items():
                if isinstance(role_data, dict) and role_data.get("position") == player_pos:
                    entity_type = str(role_data.get("entity_type") or role_key or "unknown")
                    spatial_role = str(role_data.get("role") or "unknown")
                    break
        # Fallback: check for any TRIGGER or COMPACT role
        if entity_type == "unknown":
            for role_key, role_data in roles.items():
                if isinstance(role_data, dict):
                    role_str = str(role_data.get("role") or "").lower()
                    if role_str in ("trigger", "intermediate", "collectible"):
                        entity_type = str(role_data.get("entity_type") or role_key or "unknown")
                        spatial_role = role_str
                        break

    archetype = str(solve_ctx.get("archetype") or "unknown")

    lesson_data = {
        "lesson_type": "action_effect",
        "action": action_id,
        "effect_class": effect_class,
        "n_cells_changed": n_changed,
        "new_colors": delta_summary.get("new_colors") or [],
        "removed_colors": delta_summary.get("removed_colors") or [],
        "direction": direction,
        "reward_signal": float(reward),
        "entity_type": entity_type,
        "spatial_role": spatial_role,
        "puzzle_archetype": archetype,
        "task_id": str(observation.get("task_id") or ""),
        "dataset_id": str(observation.get("dataset_id") or ""),
        "step": step,
    }

    try:
        await self.brain.upsert_lesson(
            content=f"[ACTION_EFFECT] step={step} action={action_id} effect={effect_class} n_changed={n_changed} entity={entity_type} archetype={archetype}",
            lesson_type="action_effect",
            metadata=lesson_data,
            session_id=self.session_id,
        )
        self._emit_trace_event(
            "operation",
            "action_effect_written",
            {"step": step, "action": action_id},
            {"effect_class": effect_class, "entity_type": entity_type},
        )
    except Exception as exc:
        logger.warning("B211: failed to write ActionEffect record: %s", exc)
```

### Call site in `perceive_step_response()`

In the existing `perceive_step_response()` method (added by B202), after the `_record_write_event` call for `notify_turn`, add:

```python
# B211: Write structured action_effect record for graph inference (B212)
await self._write_action_effect_record(
    observation=observation,
    action_id=action_id,
    reward=reward,
    step=step,
    delta_summary=delta_summary,
)
```

That's the complete change.

### tool_rules update

In `_build_orchestration_report()` (runner.py), add `"upsert_lesson"` to tool_rules with:
```python
"upsert_lesson": {
    "owner": "SideQuests",
    "allowed_modes": ["write"],
    "allowed_phases": ["perceive", "evaluate", "finalization"],
},
```

## Concrete File Changes

| File | Lines | Change |
|------|-------|--------|
| `agents/arc3/orchestrator.py` | ~1370 | New `_write_action_effect_record()` method (~55 lines) |
| `agents/arc3/orchestrator.py` | Inside `perceive_step_response()` | Add 4-line call after existing `_record_write_event` |
| `agents/arc3/runner.py` | `_build_orchestration_report()` tool_rules dict | Add `upsert_lesson` entry |

## Test

```python
async def test_perceive_step_response_writes_action_effect(mock_brain, sample_observation):
    """B211: per-step perceive must write a typed action_effect lesson."""
    orch = ARCOrchestrator(mock_brain, MockLLM(), ...)
    sample_observation["state"] = "NOT_FINISHED"
    # Simulate a FrameDelta with 48 cells changed (large_transformation)
    from agents.arc3.grid_analysis import FrameDelta
    delta = FrameDelta(apparent_effect="rotation", n_cells_changed=48, direction=None,
                       new_colors_introduced=[8], colors_removed=[])
    orch._frame_deltas = [delta]

    await orch.perceive_step_response(
        sample_observation, step=3, reward=0.0, done=False, action_id="ACTION5"
    )

    # upsert_lesson must have been called
    mock_brain.upsert_lesson.assert_called_once()
    call_kwargs = mock_brain.upsert_lesson.call_args[1]
    metadata = call_kwargs.get("metadata", {})
    assert metadata["lesson_type"] == "action_effect"
    assert metadata["action"] == "ACTION5"
    assert metadata["effect_class"] == "large_transformation"
    assert metadata["n_cells_changed"] == 48
    assert metadata["step"] == 3
```

## Validation Commands

```bash
pytest tests/test_arc3_orchestrator.py::test_perceive_step_response_writes_action_effect -v
pytest tests/test_arc3_orchestrator.py -v
python run_single_puzzle.py
python3 -c "
import json
d = json.load(open('master_timeline.json'))
effect_writes = [e for e in d if e.get('name') == 'upsert_lesson']
print(f'ActionEffect writes: {len(effect_writes)}')
for w in effect_writes[:3]:
    print(w.get('what', '')[:200])
"
```

## Risks

- `upsert_lesson` signature: confirm it accepts `content`, `lesson_type`, `metadata`, `session_id` params. Check `mcp_engine/tools/` for the actual tool handler signature.
- If `upsert_lesson` is not currently in `BrainClientProtocol`, add it. Check `benchmarks/arc3/adapter.py` `BrainClientProtocol` definition.
