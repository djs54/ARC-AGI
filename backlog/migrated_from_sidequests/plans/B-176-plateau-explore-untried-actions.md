# Plan for B176 — Plateau Policy: Force Exploration of Untried Actions

## Card Metadata

- **Card ID**: B176
- **Priority**: P1
- **Dependencies**: None

## Summary

`_score_action_families()` initializes all actions at 0.0. Untried actions stay at 0.0 — identical to tried-and-failed ones. Plateau mode locks onto the action with the highest `avg_meaningful_change` (even if it's useless for the player) and never explores alternatives. Fix: add exploration bonus for untried actions and decay the lock threshold.

## Current State

### Score initialization (solver.py:2277)

```python
scores = {aid: 0.0 for aid in available_actions}
```

All actions start at 0.0. Only actions present in `observed_action_effects` get modified.

### Scoring logic (solver.py:2281-2306)

```python
for effect in observed_effects:
    aid = effect.get("action")
    if aid not in scores: continue
    avg_reward = float(effect.get("avg_reward", 0.0) or 0.0)
    scores[aid] += avg_reward * 2.0
    avg_change = float(effect.get("avg_meaningful_change", 0.0) or 0.0)
    scores[aid] += avg_change * 0.5
    zero_streak = int(effect.get("zero_reward_streak", 0) or 0)
    if zero_streak >= 2:
        scores[aid] -= (zero_streak * 0.25)
    rank_score = float(effect.get("rank_score", 0.0) or 0.0)
    scores[aid] += rank_score * 0.3
    if effect.get("value_status") in {"low_value", "ineffective"}:
        scores[aid] -= 0.5
```

ACTION4: `avg_meaningful_change * 0.5` = positive score (42 cells changed)
ACTION2/ACTION3: no entry in `observed_effects` → stay at 0.0

### Plateau lock threshold (solver.py:2641)

```python
if best_candidate and best_score > current_score + 0.5:
    unlock_reason = f"evidence shift: ..."
```

The 0.5 threshold is fixed. Untried actions at 0.0 can never outcompete a locked action at 0.1+.

## Technical Approach

### Step 1: Add exploration bonus in `_score_action_families()` (solver.py:2277-2306)

After the `observed_effects` loop, add:

```python
# Exploration bonus: untried actions during plateau get a curiosity score
tried_actions = {e.get("action") for e in observed_effects}
zero_reward_streak = context.get("consecutive_zero_reward_steps", 0) or 0

if zero_reward_streak >= 5:  # Only during sustained plateau
    for aid in available_actions:
        if aid not in tried_actions:
            exploration_bonus = 0.3 + min(zero_reward_streak * 0.02, 0.2)  # 0.3 to 0.5
            scores[aid] += exploration_bonus
            # Trace for debugging
            if hasattr(self, '_trace'):
                self._trace("explore_bonus_applied", "plateau_policy",
                           {"action": aid, "bonus": exploration_bonus, "streak": zero_reward_streak})
```

### Step 2: Increase penalty for long zero-reward streaks (solver.py:2294-2296)

```python
zero_streak = int(effect.get("zero_reward_streak", 0) or 0)
if zero_streak >= 2:
    scores[aid] -= (zero_streak * 0.25)
# NEW: Accelerated penalty for very long streaks
if zero_streak >= 10:
    scores[aid] -= 1.0  # Effectively disqualify long-stuck actions
```

### Step 3: Decay plateau lock threshold (solver.py:2629-2656)

Add a lock duration counter and threshold decay:

```python
# In __init__ or reset:
self._plateau_lock_step: int = 0
self._plateau_lock_duration: int = 0

# In solve(), before the lock check:
if self._plateau_locked_family is not None:
    self._plateau_lock_duration += 1
else:
    self._plateau_lock_duration = 0

# Replace fixed 0.5 threshold:
lock_threshold = max(0.1, 0.5 - (self._plateau_lock_duration * 0.05))
if best_candidate and best_score > current_score + lock_threshold:
    unlock_reason = f"evidence shift (threshold={lock_threshold:.2f}): ..."
```

After 8 locked steps: threshold = 0.1. Untried actions at 0.3+ bonus can now win.

### Step 4: Tests

Create `tests/test_b176_plateau_explore_untried.py`:

1. Test that untried actions get exploration bonus >= 0.3 during plateau (streak >= 5)
2. Test that untried actions score higher than actions with zero_streak >= 10
3. Test that lock threshold decays from 0.5 to 0.1 over 8 steps
4. Test that after threshold decay, an untried action can win the plateau lock
5. Test no exploration bonus when streak < 5 (normal mode)
6. Test regression: plateau lock still works normally for short streaks

## Verification

```bash
pytest tests/test_b176_plateau_explore_untried.py -v
pytest tests/test_arc3_solver.py -v  # regression
```
