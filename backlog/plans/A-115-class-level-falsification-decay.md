# Plan: A-115 — Class-Level Falsification Decay

## Card metadata

- **Card:** A115
- **Priority:** P1
- **Layer:** ARC runtime
- **Depends on:** A113
- **Intended executor:** Haiku subagent

## Summary

Extend A113's per-action falsification tracking to class-level penalties. When action classes (e.g., exploration, manipulation) accumulate systematic failures, compound the penalty to deprioritize the entire class and redirect solver effort to more promising strategies.

## Implementation approach

### Step 1: Track class-level penalties

In `agents/arc3/orchestrator.py`, add:

```python
# In ARCOrchestrator.__init__:
self._class_falsification_penalty = {}  # {class_name: penalty_score}
self._class_penalty_accumulator = {}    # {class_name: accumulated_failures}

def _apply_class_penalty(self, action_class: str) -> float:
    """Get the penalty multiplier for an action class."""
    if action_class not in self._class_falsification_penalty:
        return 1.0
    
    penalty = self._class_falsification_penalty[action_class]
    # Penalty ranges from 1.0 (no penalty) to 0.1 (maximum penalty)
    return max(0.1, 1.0 - (penalty * 0.1))

def _record_class_failure(self, action_class: str):
    """Record failure for a class and accumulate penalty."""
    if action_class not in self._class_penalty_accumulator:
        self._class_penalty_accumulator[action_class] = 0
    
    self._class_penalty_accumulator[action_class] += 1
    
    # Compound penalty when accumulation threshold is reached
    if self._class_penalty_accumulator[action_class] >= 3:
        current_penalty = self._class_falsification_penalty.get(action_class, 0)
        self._class_falsification_penalty[action_class] = min(9, current_penalty + 1)
        logger.info("Class %s penalty compounded to %.1f", 
                   action_class, self._apply_class_penalty(action_class))
```

### Step 2: Apply class penalty in action scoring

In `agents/arc3/solver.py`, modify action value calculation:

```python
def _score_action(self, action_class: str, base_score: float) -> float:
    """Apply class penalties to action scores."""
    penalty = self.orchestrator._apply_class_penalty(action_class)
    penalized_score = base_score * penalty
    return penalized_score
```

### Step 3: Decay penalty over time

In orchestrator's step-completion logic:

```python
def _decay_class_penalties(self):
    """Decay penalties when classes produce successes."""
    for class_name, penalty in list(self._class_falsification_penalty.items()):
        if penalty > 0:
            # Slow decay: reduce by 0.1 per successful step
            self._class_falsification_penalty[class_name] = max(0, penalty - 0.1)
```

## Concrete file edits

1. **`agents/arc3/orchestrator.py`**
   - Add `_class_falsification_penalty` and `_class_penalty_accumulator` dicts to `__init__`
   - Add `_apply_class_penalty()` method
   - Add `_record_class_failure()` method
   - Add `_decay_class_penalties()` method
   - Call `_decay_class_penalties()` on each successful step

2. **`agents/arc3/solver.py`**
   - Modify action-scoring method to call `_score_action()` with orchestrator penalty
   - Apply penalty before final action selection

3. **`tests/test_a115_class_level_falsification_decay.py`**
   - Test that 3 failures in a class trigger compound penalty
   - Test that penalty reduces action class score
   - Test that penalty decays on successful steps

## Tests to add or run

- `tests/test_a115_class_level_falsification_decay.py`
- `make test-a`

## Validation commands

```bash
pytest tests/test_a115_class_level_falsification_decay.py -v
make test-a
```

## Assumptions/defaults

- Action classes are named (e.g., "exploration", "manipulation")
- Base penalty accumulation threshold is 3 failures
- Penalty multiplier ranges from 1.0 (no penalty) to 0.1 (max penalty)
- Decay rate is 0.1 per successful step
