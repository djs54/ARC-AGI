# Plan for B161 — Goal-Directed Navigation

## Card Metadata
- **Card**: B161
- **Priority**: P0
- **Dependencies**: None

## Summary

The agent moves the player through the maze but wanders aimlessly. Add spatial reasoning: per-step player position tracking, directional guidance in prompts, wall detection, and ACTION5 effect analysis.

## Corrected Baseline

Live smoke `live_qwen25_7b_smoke_1775330224` — actions DO produce pixel changes:
- ACTION3(left) → 48px, ACTION4(right) → 72px, ACTION1(up) → 49px (movement)
- ACTION5(interact) → 72-96px (significant game effect)
- ACTION1(up) → 0px, ACTION5 → 0px (wall/no-op)
- `no_progress_step_count: 15` means reward=0 (never won), NOT pixels_changed=0

Game context (from UI screenshots):
- Maze navigation, 7 levels, energy bar, directional controls + interact
- HELP screen says: "Available Controls: ↑↓←→. Discover controls, rules, and goal."
- api_knowledge.py already has action mappings but they don't reach the LLM effectively

## Technical Approach

### Step 1: Per-step player position tracking

In `agents/arc3/orchestrator.py`, after `record_step_result()`:

```python
def _update_player_position(self, observation: dict):
    """Track player centroid after each step."""
    grid = observation.get("grid")
    if not grid:
        return

    # Use solver's identified player color
    player_color = None
    for color_id, role in (self._solve_context or {}).get("object_roles", {}).items():
        if role.get("role") == "player":
            player_color = int(color_id)
            break

    if player_color is None:
        return

    # Compute centroid of player color
    rows, cols, count = 0.0, 0.0, 0
    for r, row in enumerate(grid):
        for c, val in enumerate(row):
            if val == player_color:
                rows += r
                cols += c
                count += 1

    if count > 0:
        self._player_position = (rows / count, cols / count)
```

### Step 2: Directional guidance in prompt

In `build_action_packet()`, add a new NAVIGATION block when positions are known:

```python
if self._player_position and self._goal_position:
    pr, pc = self._player_position
    gr, gc = self._goal_position
    dr = "up" if gr < pr else "down" if gr > pr else "aligned"
    dc = "left" if gc < pc else "right" if gc > pc else "aligned"
    nav_text = (
        f"Player is at approximately row {pr:.0f}, col {pc:.0f}. "
        f"Goal appears near row {gr:.0f}, col {gc:.0f}. "
        f"You need to move {dr} and {dc} to reach it."
    )
    packet.blocks.append(ContentBlock(type="NAVIGATION", content=nav_text))
```

Add "NAVIGATION" to the `ordered_keys` list in `PromptPacket.render()` after "SOLVE_CONTEXT".

### Step 3: Movement history summary

Track which actions produced movement vs hit walls:

```python
def _build_movement_summary(self) -> str:
    """Summarize which directions worked and which hit walls."""
    action_names = {"ACTION1": "up", "ACTION2": "down", "ACTION3": "left", "ACTION4": "right", "ACTION5": "interact"}
    lines = []
    for step in self._step_history[-5:]:  # Last 5 steps
        aid = step.get("action_id", "?")
        fa = step.get("frame_analysis", {})
        px = fa.get("pixels_changed", 0)
        name = action_names.get(aid, aid)
        if px == 0:
            lines.append(f"{name}: blocked (wall/no-op)")
        else:
            lines.append(f"{name}: moved ({px} pixels changed)")
    return "\n".join(lines)
```

Inject this into the prompt as part of the HISTORY block.

### Step 4: ACTION5 effect analysis

When ACTION5 produces >30 pixel changes:

```python
if action_id == "ACTION5" and pixels_changed > 30:
    # Log what changed: which colors appeared/disappeared, bounding box
    self._last_interact_effect = {
        "pixels_changed": pixels_changed,
        "new_colors": frame_analysis.get("new_colors_introduced", []),
        "removed_colors": frame_analysis.get("colors_removed", []),
        "step": step_num,
    }
```

Include in next prompt: "ACTION5 (interact) caused a major change: {pixels_changed} pixels, new colors: {colors}"

### Step 5: Goal position extraction

Extract goal position from solver's ObjectRoleMapper:

```python
def _update_goal_position(self):
    """Extract goal position from solve context."""
    for color_id, role in (self._solve_context or {}).get("object_roles", {}).items():
        if role.get("role") == "goal" and role.get("estimated_position"):
            pos = role["estimated_position"]
            self._goal_position = (pos["row"], pos["col"])
            return
```

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/orchestrator.py` | Add `_update_player_position()`, `_update_goal_position()`, `_build_movement_summary()`, NAVIGATION block in `build_action_packet()`, ACTION5 effect tracking, add "NAVIGATION" to ordered_keys |
| `agents/arc3/solver.py` | No changes needed — ObjectRoleMapper already tracks roles |
| `tests/test_b161_goal_directed_nav.py` | New: test position tracking, test directional guidance in prompt, test movement summary, test ACTION5 effect logging |

## Acceptance Criteria

1. `_player_position` is updated after each step when player color is known
2. NAVIGATION block appears in prompt when both player and goal positions are known
3. Movement history shows "up: blocked, left: moved (48px)" style summaries
4. ACTION5 effects >30px are logged and included in next prompt
5. `pytest tests/test_b161_goal_directed_nav.py tests/test_arc3_orchestrator.py -q` all pass

## Validation Commands

```bash
pytest tests/test_b161_goal_directed_nav.py -v
pytest tests/test_arc3_orchestrator.py -q
```

## Risks / Constraints

- Player color may not be identified until step 3+ (depends on ObjectRoleMapper). Navigation guidance only activates once roles are known.
- Goal position from solver may be approximate. Use "approximately" language in prompts.
- Movement summary adds ~100 tokens to prompt. Worth it for directional awareness.
