# Plan: A-117 — Grounding Gate on Goal Confidence

## Card metadata

- **Card:** A117
- **Priority:** P1
- **Layer:** ARC runtime
- **Depends on:** A114
- **Intended executor:** Haiku subagent

## Summary

Prevent goal confidence from increasing without empirical progress evidence. This grounds confidence updates in observable goal-distance changes rather than allowing speculative increases that lead to premature finishing.

## Implementation approach

### Step 1: Track confidence state and progress evidence

In `agents/arc3/orchestrator.py`, add:

```python
# In ARCOrchestrator.__init__:
self._current_goal_confidence = 0.0
self._last_goal_distance = None

def _is_progress_evident(self, current_distance: float | None) -> bool:
    """Check if progress is empirically evident."""
    if current_distance is None:
        return False
    
    if self._last_goal_distance is None:
        return False
    
    # Progress is evident only if distance decreased
    return current_distance < self._last_goal_distance
```

### Step 2: Gate confidence updates

In the method that updates goal confidence:

```python
def _update_goal_confidence(self, new_confidence: float) -> None:
    """Update confidence with empirical grounding gate."""
    current_distance = self._get_goal_distance()
    
    # Can only increase confidence if progress is evident
    if new_confidence > self._current_goal_confidence:
        if not self._is_progress_evident(current_distance):
            logger.debug("Confidence increase blocked: no progress evidence (distance %.2f -> %.2f)",
                        self._last_goal_distance or 0, current_distance or 0)
            return
    
    # Update was either not an increase, or had evidence
    self._current_goal_confidence = new_confidence
    self._last_goal_distance = current_distance
    logger.debug("Goal confidence updated to %.2f", new_confidence)
```

### Step 3: Apply the gate consistently

Ensure all confidence updates go through the gate:

```python
# Instead of direct assignment:
# self._current_goal_confidence = new_value

# Use:
self._update_goal_confidence(new_value)
```

## Concrete file edits

1. **`agents/arc3/orchestrator.py`**
   - Add `_current_goal_confidence` and `_last_goal_distance` to `__init__`
   - Add `_is_progress_evident()` method
   - Add `_update_goal_confidence()` method
   - Replace all direct confidence assignments with `_update_goal_confidence()` calls
   - Update `_last_goal_distance` tracking in `_get_goal_distance()`

2. **`tests/test_a117_grounding_gate_on_goal_confidence.py`**
   - Test that confidence increase without progress is blocked
   - Test that confidence increase with progress is allowed
   - Test that confidence can be maintained without new evidence
   - Test that confidence decreases without needing new evidence

## Tests to add or run

- `tests/test_a117_grounding_gate_on_goal_confidence.py`
- `make test-a`

## Validation commands

```bash
pytest tests/test_a117_grounding_gate_on_goal_confidence.py -v
make test-a
```

## Assumptions/defaults

- Goal distance is a numeric value where lower = better (closer to goal)
- Progress is defined as strictly decreasing goal distance
- Confidence is a value between 0.0 and 1.0
- The gate applies to all confidence updates, not just increases
