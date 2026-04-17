# Plan for B175 — Autopilot Wall-Blind Navigation

## Card Metadata

- **Card ID**: B175
- **Priority**: P1
- **Dependencies**: B168 (entity graph provides player centroid tracking)

## Summary

Autopilot picks the axis with the largest delta but never detects that the chosen action doesn't move the player. Wall detection uses `n_cells_changed` (whole-grid metric) which is nonzero even when player is stuck. Fix: use player centroid delta, track blocked axes persistently, and rotate to orthogonal axis.

## Current State

### Wall detection check 1 (orchestrator.py:374-381)

```python
recent_zero_px = sum(
    1
    for s in self._step_history[-2:]
    if s.get("decision_source") == "autopilot" and (s.get("frame_delta", {}).get("n_cells_changed", -1) == 0)
)
if recent_zero_px >= 2:
    self._emit_trace_event("operation", "autopilot_disengage", {"reason": "wall_collision", "consecutive_zero_px": recent_zero_px})
    return None
```

**Why it fails**: `n_cells_changed` counts total grid changes. ACTION4 causes 42 cells to change (gravity/scroll effect) even when player doesn't move. This check never fires.

### Wall detection check 2 (orchestrator.py:385-400)

```python
recent_autopilot = [
    s for s in self._step_history[-4:]
    if s.get("decision_source") == "autopilot"
]
if len(recent_autopilot) >= 3:
    positions = [...]
    if len(set(positions)) == 1 and positions[0] == ...:
        return None
```

**Why it fails**: Requires 3+ *consecutive* autopilot steps. LLM decisions interleave, resetting the count.

### Direction mapping (orchestrator.py:447-452)

```python
if abs(dr) >= abs(dc):
    action_id = "ACTION1" if dr < 0 else "ACTION2"
else:
    action_id = "ACTION3" if dc < 0 else "ACTION4"
```

Hardcoded convention, no fallback when chosen action doesn't produce expected movement.

## Technical Approach

### Step 1: Add `_blocked_axes` state to `__init__` (orchestrator.py:~155)

```python
self._blocked_axes: Dict[str, int] = {}  # {"row": step_blocked, "col": step_blocked}
self._last_autopilot_player_pos: Optional[Tuple[float, float]] = None
```

### Step 2: Replace check 1 with player centroid delta (orchestrator.py:374-381)

Replace the `n_cells_changed` check with:

```python
# Check if player actually moved since last autopilot step
if self._last_autopilot_player_pos is not None:
    last_row, last_col = self._last_autopilot_player_pos
    row_delta = abs(player_info["row"] - last_row)
    col_delta = abs(player_info["col"] - last_col)

    # Determine which axis autopilot was targeting
    was_targeting_row = abs(dr) >= abs(dc)
    target_axis_delta = row_delta if was_targeting_row else col_delta

    if target_axis_delta < 1.0:
        # Player didn't move on the target axis — wall detected
        blocked_axis = "row" if was_targeting_row else "col"
        self._blocked_axes[blocked_axis] = step
        self._emit_trace_event("operation", "autopilot_wall_detected", {
            "axis": blocked_axis,
            "player_delta": {"row": row_delta, "col": col_delta},
            "step": step,
        })
```

### Step 3: Replace check 2 — use persistent blocked-axis tracking

Remove the 3-consecutive-autopilot-steps check. The `_blocked_axes` dict already persists across interleaved steps.

### Step 4: Add axis rotation in direction selection (orchestrator.py:446-452)

```python
# Check if preferred axis is blocked
row_blocked = "row" in self._blocked_axes and (step - self._blocked_axes["row"]) < 10
col_blocked = "col" in self._blocked_axes and (step - self._blocked_axes["col"]) < 10

if abs(dr) >= abs(dc):
    if row_blocked and dc != 0:
        # Primary axis blocked, rotate to column axis
        action_id = "ACTION3" if dc < 0 else "ACTION4"
        rationale = f"{rationale_prefix}: row blocked, rotating to col axis"
    elif row_blocked:
        # Both axes blocked or no column delta — disengage
        return None
    else:
        action_id = "ACTION1" if dr < 0 else "ACTION2"
        rationale = f"{rationale_prefix}: ..."
else:
    if col_blocked and dr != 0:
        action_id = "ACTION1" if dr < 0 else "ACTION2"
        rationale = f"{rationale_prefix}: col blocked, rotating to row axis"
    elif col_blocked:
        return None
    else:
        action_id = "ACTION3" if dc < 0 else "ACTION4"
        rationale = f"{rationale_prefix}: ..."
```

### Step 5: Record player position at end of autopilot (orchestrator.py:~465)

```python
# Before return, save player position for next wall check
self._last_autopilot_player_pos = (player_info["row"], player_info["col"])
```

### Step 6: Clear blocked axes on significant movement (orchestrator.py, in step processing)

```python
# If player moved significantly (reward > 0 or centroid shifted > 3 cells), clear blocks
if reward > 0 or (centroid_shift > 3.0):
    self._blocked_axes.clear()
```

### Step 7: Tests

Create `tests/test_b175_autopilot_wall_detection.py`:

1. Test that `n_cells_changed > 0` but player centroid unchanged → wall detected
2. Test axis rotation when primary axis is blocked
3. Test blocked-axis persistence across interleaved non-autopilot steps
4. Test blocked-axis clearing on positive reward
5. Test disengage when both axes blocked
6. Test existing oscillation detection still works (regression)

## Verification

```bash
pytest tests/test_b175_autopilot_wall_detection.py -v
pytest tests/test_arc3_orchestrator.py -v  # regression
```
