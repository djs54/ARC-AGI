# Plan: A-114 — Goal Distance Tracking Fallback

## Card metadata

- **Card:** A114
- **Priority:** P1
- **Layer:** ARC runtime
- **Depends on:** A112
- **Intended executor:** Haiku subagent

## Summary

When goal distance metric becomes null during step evaluation, preserve the last-known non-null distance so the solver maintains continuity of progress signal and avoids misinterpreting missing data as lack of progress.

## Implementation approach

### Step 1: Add last-known-distance tracking

In `agents/arc3/orchestrator.py`, add state preservation:

```python
# In ARCOrchestrator.__init__:
self._last_known_goal_distance = None  # Fallback when current is null
self._current_goal_distance = None

def _get_goal_distance(self) -> float | None:
    """Get goal distance with fallback to last-known value."""
    current = self._compute_goal_distance()
    
    if current is not None:
        self._last_known_goal_distance = current
        self._current_goal_distance = current
        return current
    
    # Current is null, use fallback
    if self._last_known_goal_distance is not None:
        logger.debug("Goal distance null, using last-known: %.2f", 
                     self._last_known_goal_distance)
        return self._last_known_goal_distance
    
    return None
```

### Step 2: Use fallback in progress calculation

In orchestrator's progress-evaluation method, use the fallback:

```python
def _evaluate_progress(self):
    """Evaluate step progress with fallback support."""
    distance = self._get_goal_distance()
    
    if distance is None:
        # Treat as "unknown" not "no progress"
        return {"progress": 0, "confidence": "low"}
    
    # ... existing distance-based progress logic ...
```

## Concrete file edits

1. **`agents/arc3/orchestrator.py`**
   - Add `_last_known_goal_distance` and `_current_goal_distance` to `__init__`
   - Add `_get_goal_distance()` method with fallback logic
   - Update `_evaluate_progress()` to use `_get_goal_distance()` instead of direct computation
   - Ensure distance update calls go through the new method

2. **`tests/test_a114_goal_distance_tracking_fallback.py`**
   - Test that null distances fall back to last known
   - Test that new non-null distances replace the fallback
   - Test that consecutive nulls preserve the last known value

## Tests to add or run

- `tests/test_a114_goal_distance_tracking_fallback.py`
- `make test-a`

## Validation commands

```bash
pytest tests/test_a114_goal_distance_tracking_fallback.py -v
make test-a
```

## Assumptions/defaults

- Goal distance is computed as a numeric value (float)
- Null values are falsy (None, NaN, or similar)
- Fallback is only used when current value is null
