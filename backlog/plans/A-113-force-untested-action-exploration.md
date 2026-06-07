# Plan: A-113 — Force Untested Action Exploration

## Card metadata

- **Card:** A113
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A112
- **Intended executor:** Haiku subagent

## Summary

When ACTION6 reaches 100% falsification rate over recent trials, force the solver to explore ACTION7 as the next action. This prevents the exhausting-retry loop observed in SU15 where ACTION6 was tried repeatedly without success while alternative actions remained untested.

## Implementation approach

### Step 1: Track falsification rate per action class

In `agents/arc3/orchestrator.py`, add action-class falsification tracking:

```python
# In ARCOrchestrator.__init__:
self._action_falsification_window = {}  # {action_class: [is_falsified, is_falsified, ...]}
self._force_action7_next = False

def _record_action_falsification(self, action_class: str, was_falsified: bool):
    """Track whether action was falsified (produced no progress)."""
    if action_class not in self._action_falsification_window:
        self._action_falsification_window[action_class] = []
    
    window = self._action_falsification_window[action_class]
    window.append(was_falsified)
    
    # Keep rolling window of last 5 trials
    if len(window) > 5:
        window.pop(0)
    
    # Check if ACTION6 has 100% falsification
    if action_class == "ACTION6" and len(window) == 5 and all(window):
        self._force_action7_next = True
```

### Step 2: Add force-action7 gate in solver

In `agents/arc3/solver.py`, modify the action selection logic to respect the force flag:

```python
def _select_action(self, ...):
    """Select the best action for this step."""
    # Check if we're forcing ACTION7 exploration
    if self.orchestrator._force_action7_next and "ACTION7" in self.legal_actions:
        logger.info("Forcing ACTION7 exploration due to ACTION6 falsification")
        self._force_action7_next = False  # Clear the flag
        return "ACTION7"
    
    # ... existing action selection logic ...
```

### Step 3: Record falsification on each step completion

In `agents/arc3/orchestrator.py` after step evaluation, record whether the action was falsified:

```python
def _record_step_outcome(self, action_class: str, progress_delta: float):
    """Record action outcome and update falsification tracking."""
    was_falsified = progress_delta <= 0
    self._record_action_falsification(action_class, was_falsified)
```

### Step 4: Clear force flag on ACTION7 success

When ACTION7 is executed successfully, clear the force-exploration state:

```python
if action_class == "ACTION7" and progress_delta > 0:
    # ACTION7 succeeded, clear any pending force flags
    self._action_falsification_window["ACTION6"] = []
    self._force_action7_next = False
```

## Concrete file edits

1. **`agents/arc3/orchestrator.py`**
   - Add `_action_falsification_window` dict and `_force_action7_next` flag to `__init__`
   - Add `_record_action_falsification()` method
   - Call `_record_action_falsification()` after each step evaluation
   - Add clearing logic when ACTION7 succeeds

2. **`agents/arc3/solver.py`**
   - Modify `_select_action()` or action-proposal method to check `_force_action7_next`
   - Respect the force decision when ACTION7 is available in legal actions
   - Log when force-action7 is triggered

3. **`tests/test_a113_force_untested_action_exploration.py`**
   - Test that ACTION6 with 5 consecutive falsifications triggers force-ACTION7
   - Test that force-ACTION7 is cleared after ACTION7 executes
   - Test that force-ACTION7 does not trigger if ACTION6 has any successes in the window

## Tests to add or run

- `tests/test_a113_force_untested_action_exploration.py`
- `make test-a`

## Validation commands

```bash
pytest tests/test_a113_force_untested_action_exploration.py -v
make test-a
```

## Assumptions/defaults

- ACTION6 and ACTION7 are available in the puzzle's legal actions
- A step is considered "falsified" if progress_delta <= 0
- The rolling window is last 5 trials
- Force-action7 only triggers if the window is complete (5 trials) and all are falsified
