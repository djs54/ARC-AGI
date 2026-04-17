# Plan for B149 — Action Fatigue: Penalize Exhausted Zero-Reward Actions

## Card Metadata

- **Card ID**: B149
- **Priority**: P0
- **Dependencies**: None (benefits from B142 evidence-floor, but independent)

## Summary

The agent locks onto a single zero-reward action (e.g. ACTION6) for 15+ steps because no existing guard penalizes per-action exploitation failure. This plan adds a fatigue counter per action, a threshold-based override in the action enforcement policy, and a plateau escape hatch when the locked family itself is fatigued.

## Verified Baseline

From the latest live run:
- `no_progress_step_count: 15`, plateau locked on ACTION6
- Every step: ACTION6, reward = 0
- B133 repetition gate never fires (frame hash changes between steps)
- B141 blocked-action threshold not reached (global counter, not per-action)
- Plateau mode (B144–B146) actively locks the agent onto ACTION6

## Technical Approach

### 1. Add fatigue tracking to orchestrator

In `agents/arc3/orchestrator.py`, add to `__init__`:

```python
# B149: Per-action fatigue tracking
self._action_fatigue: dict[str, int] = {}  # action_id -> consecutive zero-reward count
```

### 2. Update fatigue counters after each step

In the `record_step_result` method (or wherever the orchestrator processes step outcomes), update the fatigue counter:

```python
# B149: Update action fatigue
action_id = record.get("action_id")
reward = record.get("reward", 0.0)
if action_id:
    if reward > 0:
        # Productive action — reset its fatigue
        self._action_fatigue[action_id] = 0
    else:
        # Zero-reward — increment fatigue
        self._action_fatigue[action_id] = self._action_fatigue.get(action_id, 0) + 1
```

### 3. Apply fatigue penalty in `_enforce_action_policy`

After the existing B141 blocked-action check and before the B133 repetition gate, add:

```python
# B149: Action fatigue override
FATIGUE_THRESHOLD = 3
fatigue_count = self._action_fatigue.get(action_id, 0)
if fatigue_count >= FATIGUE_THRESHOLD:
    # Find the best alternative: least-fatigued available action
    alternatives = [
        a for a in available_actions
        if a != action_id and self._action_fatigue.get(a, 0) < FATIGUE_THRESHOLD
    ]
    if alternatives:
        # Prefer the action with the lowest fatigue count
        best_alt = min(alternatives, key=lambda a: self._action_fatigue.get(a, 0))
        self._emit_trace_event(
            "operation",
            "action_fatigue_override",
            {
                "fatigued_action": action_id,
                "fatigue_count": fatigue_count,
                "replacement": best_alt,
                "replacement_fatigue": self._action_fatigue.get(best_alt, 0),
            },
        )
        action.update({
            "action_id": best_alt,
            "rationale": (
                f"policy override: {action_id} has {fatigue_count} consecutive zero-reward "
                f"uses (fatigue threshold={FATIGUE_THRESHOLD}); rotating to {best_alt}."
            ),
            "decision_source": "fatigue_override",
        })
        return action
```

### 4. Plateau escape hatch

In the plateau-mode section of `_enforce_action_policy` (B144–B146 block), add a fatigue check before enforcing the locked family:

```python
# B149: Plateau fatigue escape
top_fatigue = self._action_fatigue.get(top_family, 0)
if top_fatigue >= FATIGUE_THRESHOLD and secondary and secondary in available_actions:
    secondary_fatigue = self._action_fatigue.get(secondary, 0)
    if secondary_fatigue < FATIGUE_THRESHOLD:
        self._emit_trace_event(
            "operation",
            "plateau_fatigue_escape",
            {
                "locked_family": top_family,
                "locked_fatigue": top_fatigue,
                "escape_to": secondary,
                "secondary_fatigue": secondary_fatigue,
            },
        )
        action.update({
            "action_id": secondary,
            "rationale": (
                f"plateau fatigue escape: locked family {top_family} has "
                f"{top_fatigue} zero-reward uses; trying secondary {secondary}."
            ),
            "decision_source": "fatigue_override",
        })
        return action
```

### 5. Reset fatigue on chunk switch

Wherever the active chunk changes (look for where `_active_chunk` is set to a new chunk), reset the fatigue counters:

```python
# B149: Reset fatigue on chunk switch
self._action_fatigue.clear()
```

This likely happens in:
- `solve()` method when a new chunk is generated (around the `self._active_chunk = ...` assignment)
- `_mark_active_chunk_failed()` or `reset_for_retry()` methods
- `record_step_result()` when detecting chunk transition

The cleanest place is the orchestrator's solve-phase handling, right after detecting that the active chunk description changed from the previous step.

### 6. Expose threshold as class constant

```python
class ARC3Orchestrator:
    ACTION_FATIGUE_THRESHOLD: int = 3
```

This makes it easy to tune later.

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/orchestrator.py` | Add `_action_fatigue` dict to `__init__`; update fatigue in `record_step_result`; add fatigue override in `_enforce_action_policy` (before B133 block); add plateau escape hatch in B144–B146 block; reset fatigue on chunk switch; add `ACTION_FATIGUE_THRESHOLD` class constant |
| `tests/test_b149_action_fatigue.py` | New: test fatigue increment, threshold override, plateau escape, chunk-reset, reward-resets-fatigue, no-false-positives |

## API/Schema/Test Updates

- No tool catalog changes
- No adapter allow-list changes
- No schema changes
- Trace output gains `action_fatigue_override` and `plateau_fatigue_escape` events (additive, non-breaking)

## Acceptance Criteria

- [ ] Per-action fatigue counter increments on each zero-reward step for the executed action
- [ ] After 3 consecutive zero-reward uses, `_enforce_action_policy` switches to an alternative
- [ ] When plateau locked-family is fatigued, secondary family is used instead
- [ ] Fatigue counters reset when the active chunk changes
- [ ] `action_fatigue_override` trace event emitted on fatigue-driven switch
- [ ] Productive actions (reward > 0) reset their fatigue to 0
- [ ] Existing solver/orchestrator tests pass with no regressions

## Validation Commands

```bash
python3 -m pytest tests/test_b149_action_fatigue.py -v
python3 -m pytest tests/test_arc3_orchestrator.py -q
python3 -m pytest tests/test_arc3_solver.py -q
```

## Risks / Constraints

- **Threshold tuning**: 3 is a starting value. If too aggressive, the agent won't persist long enough on actions that need a few tries to show reward. If too lenient, plateaus still happen. Can be tuned after live smoke.
- **Interaction with plateau mode**: The fatigue escape must not completely override the plateau lock — it should prefer the secondary family, not a random action. If both top and secondary are fatigued, fall through to the normal fatigue rotation logic.
- **Fatigue counter scope**: Fatigue is per-chunk, not per-run. This is correct because a new plan may legitimately want to retry an action that failed under a different strategy.

## Done When

- Fatigue counters track per-action zero-reward streaks
- Actions exceeding the threshold are replaced in enforcement policy
- Plateau locked family can be escaped when fatigued
- Chunk changes reset all fatigue
- No regressions in existing tests
