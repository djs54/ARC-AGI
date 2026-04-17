# Plan for B178 — Action Semantics Discovery for Autopilot

## Card Metadata

- **Card ID**: B178
- **Priority**: P1
- **Dependencies**: B168 (exploration agent writes ActionEffect nodes to KuzuDB)

## Summary

Autopilot assumes ACTION1=up, ACTION2=down, ACTION3=left, ACTION4=right. This is wrong for many puzzles. B168 exploration discovers actual action effects and stores them in KuzuDB `ActionEffect` nodes with `direction_row` and `direction_col`. But autopilot never reads this data. Fix: query ActionEffect at autopilot start and build a per-puzzle direction map.

## Current State

### Hardcoded direction mapping (orchestrator.py:447-452)

```python
if abs(dr) >= abs(dc):
    action_id = "ACTION1" if dr < 0 else "ACTION2"
else:
    action_id = "ACTION3" if dc < 0 else "ACTION4"
```

### ActionEffect schema (entity_graph.py / schema.py)

B168 creates `ActionEffect` nodes with properties:
- `action_id`: e.g. "ACTION1"
- `direction_row`: observed player row delta (positive = down)
- `direction_col`: observed player col delta (positive = right)
- `n_cells_changed`: total grid cells changed

### Autopilot reads roles from _solve_context (orchestrator.py:312)

```python
roles = sc.get("object_roles") or {}
```

Note: This is still reading from `_solve_context` dict, not `solve_engine._object_roles`. (Separate issue, partially addressed in B175 scope.)

## Technical Approach

### Step 1: Add `_action_direction_map` to `__init__` (orchestrator.py:~155)

```python
self._action_direction_map: Optional[Dict[str, Tuple[float, float]]] = None
# Maps action_id -> (row_delta, col_delta) from empirical observation
```

### Step 2: Add query method to entity_graph.py

```python
async def get_action_directions(self, task_id: str, level: int) -> Dict[str, Tuple[float, float]]:
    """Query ActionEffect nodes to get observed direction for each action.
    Returns {action_id: (avg_row_delta, avg_col_delta)}."""
    query = """
    MATCH (a:ActionEffect)
    WHERE a.task_id = $task_id AND a.level = $level
    RETURN a.action_id, avg(a.direction_row) AS avg_dr, avg(a.direction_col) AS avg_dc
    """
    results = await self._db.execute(query, {"task_id": task_id, "level": level})
    return {row[0]: (row[1], row[2]) for row in results}
```

### Step 3: Load action map at start of `_try_autopilot()` (orchestrator.py:~311)

```python
# Load empirical action directions (once per level, cached)
if self._action_direction_map is None and self._entity_graph:
    try:
        self._action_direction_map = await self._entity_graph.get_action_directions(
            task_id=self._task_id, level=self._current_level
        )
    except Exception:
        self._action_direction_map = {}  # Fall back to default
```

Note: `_try_autopilot` is sync. Either make it async or load the map in the async `act()` method before calling `_try_autopilot()`. Recommend loading in `act()` and passing as parameter.

### Step 4: Replace hardcoded mapping (orchestrator.py:447-452)

```python
def _pick_action_for_direction(self, dr: float, dc: float, available_actions: List[str]) -> Optional[str]:
    """Pick the action that best moves the player in the (dr, dc) direction."""
    if self._action_direction_map:
        best_action = None
        best_dot = -float('inf')
        for aid, (a_dr, a_dc) in self._action_direction_map.items():
            if aid not in available_actions:
                continue
            # Dot product: how aligned is this action with desired direction?
            dot = dr * a_dr + dc * a_dc
            if dot > best_dot:
                best_dot = dot
                best_action = aid
        if best_action and best_dot > 0:
            return best_action

    # Fallback to convention
    if abs(dr) >= abs(dc):
        return "ACTION1" if dr < 0 else "ACTION2"
    else:
        return "ACTION3" if dc < 0 else "ACTION4"
```

Replace lines 447-452 with:

```python
action_id = self._pick_action_for_direction(dr, dc, available_actions)
if action_id is None:
    return None
rationale = f"{rationale_prefix}: target is ({dr:.1f}, {dc:.1f}) away, {'empirical' if self._action_direction_map else 'default'} mapping -> {action_id}"
```

### Step 5: Reset map on level change

In the level transition handler:

```python
self._action_direction_map = None  # Re-discover on new level
```

### Step 6: Tests

Create `tests/test_b178_action_semantics.py`:

1. Test `_pick_action_for_direction()` with empirical map where ACTION2 moves up (reversed convention)
2. Test fallback to convention when no empirical data
3. Test dot product correctly selects diagonal-ish actions
4. Test map reset on level change
5. Test graceful handling when entity_graph unavailable (db=None)
6. Test regression: default convention produces same actions as before when no map

## Verification

```bash
pytest tests/test_b178_action_semantics.py -v
pytest tests/test_arc3_orchestrator.py -v  # regression
```
