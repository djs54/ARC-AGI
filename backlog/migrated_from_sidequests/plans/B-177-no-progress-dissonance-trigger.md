# Plan for B177 — Fix No-Progress Escalation Tier Ladder

## Card Metadata

- **Card ID**: B177
- **Priority**: P1
- **Dependencies**: None

## Summary

The no-progress escalation ladder (tiers 1-3) cycles every 8 steps without producing real behavior changes. Tier 1 sets dissonance on a dict the SolveEngine doesn't read. Tier 2 blocks actions but the blocks reset at tier 3. Tier 3 resets the counter, preventing cumulative escalation. Fix all three tiers and add an absolute threshold.

## Current State

### Tier 1 — force_replan (orchestrator.py:1675-1682)

```python
if self._consecutive_no_progress_steps >= 3:
    if self._solve_context:
        self._solve_context["dissonance"] = True
```

**Bug**: `_solve_context["dissonance"]` is read by `_should_check_loop` (line 1652) but NOT by `SolveEngine.DissonanceDetector.update()` at `solver.py:2384`. The SolveEngine uses its own `should_replan` logic based on `hypothesis_context` signals, not `_solve_context["dissonance"]`.

### Tier 2 — block_action (orchestrator.py:1684-1693)

```python
if self._consecutive_no_progress_steps >= 5:
    if self._step_history:
        last_action = self._step_history[-1].get("action_id")
        if last_action:
            self._blocked_actions.add(last_action)
```

**Bug**: `_blocked_actions` (line 159) is checked in `_pick_action()` but NOT in `_try_autopilot()`. Since autopilot runs first (line ~550 in the act loop), it picks the blocked action before `_pick_action()` ever sees the block set.

### Tier 3 — abandon_chunk + reset (orchestrator.py:1695-1702)

```python
if self._consecutive_no_progress_steps >= 8:
    self._mark_active_chunk_failed("no_progress_abandon")
    self._consecutive_no_progress_steps = 0  # reset ladder
```

**Bug**: Counter resets every 8 steps. Over 146 steps, ladder cycled ~18 times with no cumulative effect.

### _blocked_actions initialization (orchestrator.py:159)

```python
self._blocked_actions: set = set()
```

Never cleared explicitly — but the counter reset at tier 3 means tier 2 blocks accumulate but then the agent runs out of tier 2 triggers. After tier 3 reset, the counter climbs back from 0, re-adding the same actions.

## Technical Approach

### Step 1: Connect tier 1 to SolveEngine (orchestrator.py:1675-1682)

Add a flag that gets passed to `solve()`:

```python
if self._consecutive_no_progress_steps >= 3:
    self._force_replan = True  # NEW: read by SolveEngine
    self._emit_trace_event(...)
```

In the `act()` method where `solve()` is called (~line 560), pass the flag:

```python
if hasattr(self, '_force_replan') and self._force_replan:
    hypothesis_context["orchestrator_force_replan"] = True
    self._force_replan = False
```

In `solver.py:2384`, add:

```python
should_replan, dissonance_reason = self.dissonance_detector.update(...)
# B177: Accept orchestrator escalation
if hypothesis_context.get("orchestrator_force_replan"):
    should_replan = True
    dissonance_reason = dissonance_reason or "orchestrator_no_progress_escalation"
```

### Step 2: Make tier 2 blocks checked by autopilot (orchestrator.py:~454)

In `_try_autopilot()`, before returning the action:

```python
if action_id in self._blocked_actions:
    # Try orthogonal axis
    if action_id in ("ACTION1", "ACTION2"):
        alt = "ACTION3" if dc < 0 else "ACTION4"
    else:
        alt = "ACTION1" if dr < 0 else "ACTION2"
    if alt not in self._blocked_actions and alt in available_actions:
        action_id = alt
        rationale = f"{rationale_prefix}: primary blocked, using alternative"
    else:
        return None  # All directions blocked, disengage autopilot
```

### Step 3: Replace tier 3 reset with escalating intervention (orchestrator.py:1695-1702)

```python
if self._consecutive_no_progress_steps >= 8:
    self._mark_active_chunk_failed("no_progress_abandon")
    # DON'T reset to 0 — cap at 8 so tiers 1-2 keep firing
    # Instead, escalate: clear stale state
    self._blocked_axes.clear() if hasattr(self, '_blocked_axes') else None
    if hasattr(self, '_plateau_locked_family'):
        self.solve_engine._plateau_locked_family = None
    self._emit_trace_event(
        "operation", "no_progress_escalation",
        {"tier": 3, "action": "strategy_reset", "steps": self._consecutive_no_progress_steps}
    )
    # Don't reset counter — let it accumulate for tier 4
```

### Step 4: Add absolute threshold (new tier 4)

```python
if self._consecutive_no_progress_steps >= 20:
    # Tier 4: Nuclear option — re-evaluate everything
    self.solve_engine._archetype_confidence *= 0.5  # Force re-classification
    self.solve_engine._victory_condition = None      # Force re-inference
    self._blocked_actions.clear()                    # Fresh start
    self._consecutive_no_progress_steps = 10         # Reset to tier 2 level, not 0
    self._emit_trace_event(
        "operation", "no_progress_escalation",
        {"tier": 4, "action": "full_reset", "steps": 20}
    )
```

### Step 5: Blocked actions persist until positive reward

In the reward processing section (~line 4612):

```python
if progress:
    self._consecutive_no_progress_steps = 0
    self._blocked_actions.clear()  # Clear blocks on progress
```

Only clear `_blocked_actions` on positive reward, not on tier 3 counter reset.

### Step 6: Tests

Create `tests/test_b177_no_progress_escalation.py`:

1. Test tier 1 sets `orchestrator_force_replan` in hypothesis_context
2. Test tier 2 blocked actions are respected by `_try_autopilot()`
3. Test tier 3 does NOT reset counter to 0
4. Test tier 4 fires at 20 steps and resets archetype confidence
5. Test blocked actions only clear on positive reward
6. Test orchestrator_force_replan reaches SolveEngine and sets should_replan=True
7. Test regression: normal escalation still fires at correct thresholds

## Verification

```bash
pytest tests/test_b177_no_progress_escalation.py -v
pytest tests/test_arc3_orchestrator.py -v  # regression
pytest tests/test_arc3_solver.py -v        # regression
```
