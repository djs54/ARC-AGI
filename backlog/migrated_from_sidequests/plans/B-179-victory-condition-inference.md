# Plan for B179 — Victory Condition Inference Threshold Fix

## Card Metadata

- **Card ID**: B179
- **Priority**: P2
- **Dependencies**: None

## Summary

`VictoryHypothesizer.CALL_THRESHOLD = 0.65` prevents victory condition inference for puzzles where archetype confidence is in the common 0.5-0.65 range. The fallback path (`should_replan`) is also broken (B177). Fix: lower threshold, add step-based and zero-progress triggers.

## Current State

### CALL_THRESHOLD (solver.py:1009)

```python
class VictoryHypothesizer:
    """...
    Called once when archetype confidence > CALL_THRESHOLD.
    """
    CALL_THRESHOLD: float = 0.65
```

### Inference trigger (solver.py:2399-2407)

```python
need_victory_hypothesis = (
    self._victory_condition is None
    and self._archetype_confidence >= VictoryHypothesizer.CALL_THRESHOLD
) or (
    should_replan
    and (self._victory_condition is None or self._victory_condition.confidence < 0.5)
)
```

Path 1: Archetype confidence >= 0.65 — often not met (traced puzzle had ~0.6)
Path 2: `should_replan` must be True — but dissonance signal is broken (B177)

### Victory condition default

If neither path fires, `self._victory_condition` stays `None`. The `SolveContext` returned by `solve()` includes `victory_condition=None` → serialized as "unknown" at 0.0 confidence.

## Technical Approach

### Step 1: Lower CALL_THRESHOLD (solver.py:1009)

```python
CALL_THRESHOLD: float = 0.45
```

This matches the practical output range of the archetype classifier for spatial navigation puzzles (0.5-0.6).

### Step 2: Add step-based fallback trigger (solver.py:2399-2407)

```python
need_victory_hypothesis = (
    self._victory_condition is None
    and self._archetype_confidence >= VictoryHypothesizer.CALL_THRESHOLD
) or (
    should_replan
    and (self._victory_condition is None or self._victory_condition.confidence < 0.5)
) or (
    # B179: Step-based fallback — after 15 steps, attempt regardless of confidence
    self._victory_condition is None
    and step >= 15
    and self._archetype is not None
) or (
    # B179: Zero-progress trigger — being stuck suggests we don't understand win state
    self._victory_condition is None
    and self._recent_zero_reward_streak() >= 5
    and self._archetype is not None
)
```

### Step 3: Add trace event for trigger path (solver.py:2407+)

```python
if need_victory_hypothesis:
    trigger_reason = "archetype_threshold"
    if self._archetype_confidence < VictoryHypothesizer.CALL_THRESHOLD:
        if step >= 15:
            trigger_reason = "step_fallback"
        elif self._recent_zero_reward_streak() >= 5:
            trigger_reason = "zero_progress"
        elif should_replan:
            trigger_reason = "replan"
    self._trace("victory_inference_trigger", "victory_hypothesis",
                {"step": step, "trigger": trigger_reason,
                 "archetype_conf": self._archetype_confidence})
```

### Step 4: Guard against repeated LLM calls

The victory hypothesis involves an LLM call (expensive). Add a cooldown to prevent repeated calls:

```python
# In __init__:
self._last_victory_attempt_step: int = -100

# In the trigger check, add:
and (step - self._last_victory_attempt_step) >= 10  # At most every 10 steps

# After the LLM call:
self._last_victory_attempt_step = step
```

### Step 5: Tests

Create `tests/test_b179_victory_condition_inference.py`:

1. Test inference fires with archetype confidence 0.5 (above new threshold 0.45)
2. Test inference does NOT fire with archetype confidence 0.3 (below 0.45) and step < 15
3. Test step-based fallback fires at step 15 when confidence is 0.3
4. Test zero-progress trigger fires at streak 5 when confidence is 0.3
5. Test cooldown prevents repeated calls within 10 steps
6. Test regression: inference still fires at 0.65+ confidence (original behavior preserved)
7. Test trigger_reason trace event correctly identifies which path fired

## Verification

```bash
pytest tests/test_b179_victory_condition_inference.py -v
pytest tests/test_arc3_solver.py -v  # regression
```
