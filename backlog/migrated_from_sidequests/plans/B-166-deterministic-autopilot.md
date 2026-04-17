# Plan for B166 — Deterministic Autopilot

## Card Metadata
- **Card**: B166
- **Priority**: P0
- **Dependencies**: None

## Summary

When the SolveEngine has high-confidence player and goal positions, bypass the LLM entirely and compute the correct directional action (ACTION1-4) deterministically. This eliminates the fundamental bottleneck: qwen2.5:7b cannot reason spatially, so it wanders randomly even when position data is in the prompt.

## Technical Approach

### Step 1: Add `_try_autopilot()` method to orchestrator

In `agents/arc3/orchestrator.py`, add a new method that checks whether autopilot conditions are met and returns an action dict if so:

```python
def _try_autopilot(self, observation: dict, available_actions: List[str]) -> Optional[ARC3Action]:
    """B166: Deterministic navigation when player/goal positions are known.

    Returns an action dict if autopilot can determine the correct move,
    or None to fall through to the LLM.
    """
    # 1. Check solve context for player and goal roles
    sc = self._solve_context or {}
    roles = sc.get("object_roles") or {}

    player_info = None
    goal_info = None
    for color_id, role_data in roles.items():
        if role_data.get("role") == "player" and role_data.get("confidence", 0) >= 0.7:
            pos = role_data.get("estimated_position")
            if pos and pos.get("row") is not None and pos.get("col") is not None:
                player_info = {"color": color_id, "row": pos["row"], "col": pos["col"], "conf": role_data["confidence"]}
        elif role_data.get("role") == "goal" and role_data.get("confidence", 0) >= 0.7:
            pos = role_data.get("estimated_position")
            if pos and pos.get("row") is not None and pos.get("col") is not None:
                goal_info = {"color": color_id, "row": pos["row"], "col": pos["col"], "conf": role_data["confidence"]}

    if not player_info or not goal_info:
        return None  # Can't autopilot without both positions

    # 2. Check for wall collision (autopilot disengage)
    recent_zero_px = sum(
        1 for s in self._step_history[-2:]
        if s.get("decision_source") == "autopilot"
        and s.get("frame_delta", {}).get("n_cells_changed", -1) == 0
    )
    if recent_zero_px >= 2:
        self._emit_trace_event("operation", "autopilot_disengage",
            {"reason": "wall_collision", "consecutive_zero_px": recent_zero_px})
        return None  # Yield to LLM — we're hitting a wall

    # 3. Compute direction
    dr = goal_info["row"] - player_info["row"]  # negative = goal is above (need ACTION1/up)
    dc = goal_info["col"] - player_info["col"]  # negative = goal is left (need ACTION3/left)

    # 4. Determine action
    # If arrived (within 1 cell on both axes), try interact
    if abs(dr) <= 1.0 and abs(dc) <= 1.0:
        if "ACTION5" in available_actions:
            action_id = "ACTION5"
            rationale = f"autopilot: arrived near goal ({abs(dr):.1f}r, {abs(dc):.1f}c away), trying interact"
        else:
            return None
    else:
        # Pick the axis with the larger delta
        if abs(dr) >= abs(dc):
            # Prioritize vertical movement
            action_id = "ACTION1" if dr < 0 else "ACTION2"  # up if goal above, down if below
            rationale = f"autopilot: goal is {abs(dr):.1f} rows {'above' if dr < 0 else 'below'}, moving {'up' if dr < 0 else 'down'}"
        else:
            # Prioritize horizontal movement
            action_id = "ACTION3" if dc < 0 else "ACTION4"  # left if goal is left, right if right
            rationale = f"autopilot: goal is {abs(dc):.1f} cols {'left' if dc < 0 else 'right'}, moving {'left' if dc < 0 else 'right'}"

    # Validate action is available
    if action_id not in available_actions:
        return None

    self._emit_trace_event("operation", "autopilot_engage", {
        "player": {"row": player_info["row"], "col": player_info["col"], "conf": player_info["conf"]},
        "goal": {"row": goal_info["row"], "col": goal_info["col"], "conf": goal_info["conf"]},
        "delta_row": round(dr, 1),
        "delta_col": round(dc, 1),
        "chosen_action": action_id,
    })

    return {
        "action_id": action_id,
        "rationale": rationale,
        "decision_source": "autopilot",
    }
```

### Step 2: Wire autopilot into `act()` — before the mental sandbox

In `act()`, insert the autopilot check **after** building the prompt packet but **before** the mental sandbox call (around line 1388):

```python
        # B166: Deterministic autopilot — bypass LLM when player/goal positions are known
        autopilot_action = self._try_autopilot(observation, available_actions)
        if autopilot_action:
            action = autopilot_action
            sandbox_elapsed = 0.0
            candidate_action_id = action["action_id"]
            llm_source = "autopilot"

            self._emit_trace_event(
                "operation",
                "mental_sandbox",
                {"step": step_num},
                {"action_id": candidate_action_id, "decision_source": "autopilot", "skipped": True},
                0.0,
            )
        else:
            # B114/B123: Mental Sandbox reasoning loop (includes REPL)
            sandbox_start = time.time()
            action = await self._mental_sandbox(prompt, available_actions, observation)
            sandbox_elapsed = (time.time() - sandbox_start) * 1000

            candidate_action_id = action.get("action_id")
            llm_source = action.get("decision_source", "unknown")

            self._emit_trace_event(
                "operation",
                "mental_sandbox",
                {"step": step_num},
                {"action_id": candidate_action_id, "decision_source": llm_source},
                sandbox_elapsed,
            )
```

This replaces the existing sandbox call block (lines ~1388-1406).

### Step 3: Ensure autopilot actions pass through policy and guard

The autopilot action flows through `_enforce_action_policy()` and `critique_action()` like any other action. The exploration policy may try to override it, but since autopilot actions aren't `mental_sandbox_fallback`, they'll be treated like normal LLM decisions.

Important: the B154 exploration policy on level 1 forces exploration of untested actions. This could conflict with autopilot. The fix: in `_enforce_action_policy`, if `source == "autopilot"`, skip the forced exploration override. Autopilot has higher authority than exploration policy — it knows where to go.

```python
        # B166: Autopilot decisions have highest authority — skip exploration override
        if source == "autopilot":
            # Still track frame hash for repetition detection
            if current_frame_hash:
                self._action_frame_hashes[action_id] = current_frame_hash
            return action
```

Insert this at the **top** of `_enforce_action_policy()`, right after reading `source` (after line 3157).

### Step 4: Update player position from grid after each step

The SolveEngine already tracks estimated_position in object_roles, but it's based on role observation history. For more accurate autopilot, also update player position from the actual grid after each step:

This is already handled by the existing `ObjectRoleMapper` in solver.py — `_estimate_position()` computes centroid from recent observations. No change needed here as long as `_solve_context["object_roles"]` is refreshed each step (it is, via `observe_step()` in the solve phase).

### Step 5: Add tests

In `tests/test_arc3_orchestrator.py`, add a `TestAutopilot` class:

```python
class TestAutopilot:
    """B166: Deterministic autopilot tests."""

    def _make_orchestrator(self):
        brain = AsyncMock()
        llm = MagicMock()
        orch = ARCOrchestrator(brain, llm, "s1", StateSerializerForARC(), {})
        return orch

    def test_autopilot_moves_up_when_goal_above(self):
        orch = self._make_orchestrator()
        orch._solve_context = {"object_roles": {
            "14": {"role": "player", "confidence": 0.8, "estimated_position": {"row": 50.0, "col": 33.0}},
            "2": {"role": "goal", "confidence": 0.9, "estimated_position": {"row": 29.0, "col": 33.0}},
        }}
        result = orch._try_autopilot({}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"])
        assert result is not None
        assert result["action_id"] == "ACTION1"  # up
        assert result["decision_source"] == "autopilot"

    def test_autopilot_moves_right_when_goal_right(self):
        orch = self._make_orchestrator()
        orch._solve_context = {"object_roles": {
            "14": {"role": "player", "confidence": 0.8, "estimated_position": {"row": 32.0, "col": 20.0}},
            "5": {"role": "goal", "confidence": 0.9, "estimated_position": {"row": 31.0, "col": 45.0}},
        }}
        result = orch._try_autopilot({}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"])
        assert result is not None
        assert result["action_id"] == "ACTION4"  # right (larger delta on col axis)

    def test_autopilot_interacts_when_arrived(self):
        orch = self._make_orchestrator()
        orch._solve_context = {"object_roles": {
            "14": {"role": "player", "confidence": 0.8, "estimated_position": {"row": 31.0, "col": 28.0}},
            "5": {"role": "goal", "confidence": 0.9, "estimated_position": {"row": 31.0, "col": 28.5}},
        }}
        result = orch._try_autopilot({}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"])
        assert result is not None
        assert result["action_id"] == "ACTION5"  # interact (arrived)

    def test_autopilot_returns_none_when_low_confidence(self):
        orch = self._make_orchestrator()
        orch._solve_context = {"object_roles": {
            "14": {"role": "player", "confidence": 0.4, "estimated_position": {"row": 50.0, "col": 33.0}},
            "2": {"role": "goal", "confidence": 0.9, "estimated_position": {"row": 29.0, "col": 33.0}},
        }}
        result = orch._try_autopilot({}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"])
        assert result is None  # player confidence too low

    def test_autopilot_returns_none_when_no_positions(self):
        orch = self._make_orchestrator()
        orch._solve_context = {"object_roles": {
            "14": {"role": "player", "confidence": 0.8, "estimated_position": None},
            "2": {"role": "goal", "confidence": 0.9, "estimated_position": {"row": 29.0, "col": 33.0}},
        }}
        result = orch._try_autopilot({}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"])
        assert result is None

    def test_autopilot_disengages_on_wall_collision(self):
        orch = self._make_orchestrator()
        orch._solve_context = {"object_roles": {
            "14": {"role": "player", "confidence": 0.8, "estimated_position": {"row": 50.0, "col": 33.0}},
            "2": {"role": "goal", "confidence": 0.9, "estimated_position": {"row": 29.0, "col": 33.0}},
        }}
        # Simulate 2 consecutive zero-pixel autopilot steps
        orch._step_history = [
            {"decision_source": "autopilot", "frame_delta": {"n_cells_changed": 0}},
            {"decision_source": "autopilot", "frame_delta": {"n_cells_changed": 0}},
        ]
        result = orch._try_autopilot({}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"])
        assert result is None  # disengaged

    def test_autopilot_prioritizes_larger_delta(self):
        orch = self._make_orchestrator()
        orch._solve_context = {"object_roles": {
            "14": {"role": "player", "confidence": 0.8, "estimated_position": {"row": 50.0, "col": 30.0}},
            "2": {"role": "goal", "confidence": 0.9, "estimated_position": {"row": 30.0, "col": 33.0}},
        }}
        # 20 rows vs 3 cols — should prioritize vertical
        result = orch._try_autopilot({}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"])
        assert result["action_id"] == "ACTION1"  # up (larger delta)
```

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/orchestrator.py` | Add `_try_autopilot()` method (~50 lines). Replace mental sandbox call block in `act()` with autopilot-first conditional (~15 lines). Add autopilot early-return in `_enforce_action_policy()` (~5 lines). |
| `tests/test_arc3_orchestrator.py` | Add `TestAutopilot` class with 7 tests |

## API/Schema/Test Updates

- No schema changes
- No tool changes (no tool-catalog.md update needed)
- No adapter changes

## Acceptance Criteria

1. Autopilot fires when player confidence ≥ 0.7 AND goal confidence ≥ 0.7 AND both positions known
2. Correct direction: ACTION1 when goal above, ACTION2 when below, ACTION3 when left, ACTION4 when right
3. Larger-delta axis is prioritized
4. ACTION5 (interact) when within 1 cell on both axes
5. Disengages after 2 consecutive zero-pixel-change autopilot steps
6. Returns None (fall through to LLM) when conditions aren't met
7. `decision_source: "autopilot"` in trace and step history
8. Autopilot actions skip exploration policy override in `_enforce_action_policy()`
9. All existing tests pass: `pytest tests/test_arc3_*.py -q`

## Validation Commands

```bash
pytest tests/test_arc3_orchestrator.py::TestAutopilot -v
pytest tests/test_arc3_orchestrator.py tests/test_arc3_solver.py tests/test_b115_decision_guard.py -q
```

## Risks / Constraints

- **Maze walls**: Autopilot doesn't know about walls. It will try to move toward the goal and hit walls. The wall-collision disengage (2 consecutive 0px steps) handles this — it yields to the LLM which can try alternate routes. A future card could add wall-aware pathfinding.
- **Non-navigation puzzles**: Some ARC puzzles aren't about moving a player to a goal. Autopilot only fires when both player AND goal roles are identified with high confidence, so it won't interfere with non-navigation puzzles.
- **Goal position accuracy**: The SolveEngine's estimated_position is a centroid average — may be slightly off. But even approximate navigation is vastly better than random wandering.
- **ACTION6 (coordinate paint)**: Autopilot never selects ACTION6. If the puzzle requires painting, the LLM handles it after autopilot disengages.
