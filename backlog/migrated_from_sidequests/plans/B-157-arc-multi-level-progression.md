# Plan for B157 — ARC Multi-Level Game Progression

## Card Metadata

- **Card ID**: B157
- **Priority**: P0 (CRITICAL BLOCKER)
- **Dependencies**: None

## Summary

The runner exits on first WIN. ARC-AGI-3 games have 7-8 levels. Fix the game loop to play all levels, capture level transitions as implicit training data, and distribute the step budget across levels.

## Technical Approach

### 1. Multi-level game loop in runner.py

Replace the `break` on WIN with level continuation:

```python
# In _run_puzzle(), inside the step loop:

state = observation.get("state", "NOT_FINISHED")
levels_completed = observation.get("levels_completed", 0) or 0
win_levels = observation.get("win_levels", 1) or 1

if state == "WIN":
    # Capture level transition data
    level_end_grid = observation.get("grid")
    level_actions = list(orchestrator._level_action_buffer)
    orchestrator._solved_levels.append({
        "level": levels_completed,
        "start_grid": orchestrator._level_start_grid,
        "end_grid": level_end_grid,
        "actions": level_actions,
        "steps": len(level_actions),
    })

    # Check if game is complete
    if levels_completed >= win_levels:
        success = True
        self.brain.current_phase = "finalization"
        await orchestrator.hypothesis_mgr.distill_to_brain(
            orchestrator.solve_engine._object_roles
        )
        break

    # More levels remain — continue playing
    logger.info(
        "B157: Level %d/%d complete (%d actions). Continuing to next level.",
        levels_completed, win_levels, len(level_actions),
    )

    # Prepare for next level
    orchestrator._on_level_transition(
        completed_level=levels_completed,
        solved_levels=orchestrator._solved_levels,
    )
    orchestrator._level_start_grid = observation.get("grid")
    orchestrator._level_action_buffer = []
    steps_this_level = 0
    continue

elif state == "GAME_OVER":
    # Per-level retry (not full game restart)
    # ... existing retry logic ...
```

### 2. Level transition tracking in orchestrator.py

```python
class ARCOrchestrator:
    def __init__(self, ...):
        # ... existing init ...
        # B157: Level tracking
        self._solved_levels: List[Dict] = []
        self._level_start_grid = None
        self._level_action_buffer: List[str] = []
        self._current_level = 0

    def _on_level_transition(self, completed_level, solved_levels):
        """B157: Called when a level is won. Prepare for next level."""
        self._current_level = completed_level + 1

        # Emit trace for level completion
        self._emit_trace_event("operation", "level_complete", {
            "level": completed_level,
            "total_levels": len(solved_levels),
            "actions_used": solved_levels[-1]["steps"],
        })

        # Partial reset: keep learned knowledge, clear per-level state
        # DO NOT reset hypothesis_mgr — cross-level knowledge is valuable
        # DO NOT reset solve_engine — archetype/role knowledge persists
        # DO reset: per-level action buffer, step-level state
        self._step_history_this_level = []
        self._consecutive_no_progress_steps = 0
        if hasattr(self, '_action_fatigue'):
            self._action_fatigue.clear()
```

### 3. Per-level step budgeting

```python
# In runner.py, before the step loop:
total_step_budget = self.harness.config.parameters.get("max_attempts_per_puzzle", 10)
win_levels = observation.get("win_levels", 1) or 1

# Allocate more steps to later levels (weighted by difficulty)
# Level 1: fewer steps (tutorial), Level 8: more steps (harder)
# Simple approach: equal budget per level with floor of 3
steps_per_level = max(3, total_step_budget // win_levels)

# Track remaining budget
remaining_budget = total_step_budget
steps_this_level = 0
```

### 4. Capture level start grid

```python
# In runner.py, after initial frame and after each level transition:
orchestrator._level_start_grid = observation.get("grid")
orchestrator._level_action_buffer = []
```

### 5. Record actions per level

```python
# In runner.py, after each action:
orchestrator._level_action_buffer.append(action.get("action_id", "unknown"))
```

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/runner.py` | Multi-level game loop (continue after WIN if levels remain); per-level step budgeting; level transition capture; action buffer per level |
| `agents/arc3/orchestrator.py` | Add `_solved_levels`, `_level_start_grid`, `_level_action_buffer`, `_current_level`; add `_on_level_transition()` method; partial reset between levels |
| `tests/test_b157_multi_level_progression.py` | NEW: test level continuation, transition capture, budget distribution, partial reset |

## Validation Commands

```bash
python3 -m pytest tests/test_b157_multi_level_progression.py -v
python3 -m pytest tests/test_arc3_orchestrator.py -q
```

## Risks / Constraints

- **API behavior after WIN**: We assume the API automatically serves the next level after WIN. If it requires a RESET to advance, the code needs to issue a RESET with the same guid after WIN.
- **Step budget vs level count**: With 10 total steps and 8 levels, the per-level budget is very tight (~1-2 steps per level). The agent must solve early levels quickly. This is actually aligned with RHAE scoring — efficiency on easy levels is rewarded.
- **State carryover**: HypothesisManager and SolveEngine state should persist across levels (learned action effects, archetype classification). Only per-level counters reset.

## Done When

- Runner continues after WIN if more levels remain
- Grid snapshots captured at each level boundary
- Action sequences recorded per level
- Step budget distributed across levels
- Orchestrator receives level context
- All tests pass
