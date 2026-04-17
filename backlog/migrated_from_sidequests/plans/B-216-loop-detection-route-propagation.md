# B-216 — Loop Detection Route Propagation: Implementation Plan

- **Card:** backlog/B216.md
- **Priority:** P1
- **Dependencies:** B215

## Summary

Make loop detection structurally actionable: when the same action family is chosen for K consecutive steps with no board change, the route phase must select a different family on the next step.

## Technical Approach

### 1. Track consecutive same-family no-change streak

File: `agents/arc3/solver.py`

Add fields in `__init__`:
```python
self._LOOP_ESCAPE_THRESHOLD: int = 3       # consecutive same-family + zero-change steps before escape
self._loop_no_change_streak: int = 0       # counter: consecutive steps with same family and n_cells_changed == 0
self._loop_streak_family: Optional[str] = None  # which family is accumulating the streak
self._loop_escaped_blacklist: set[str] = set()  # families blacklisted for next chunk selection
```

### 2. Increment/reset streak counter at evaluate time

After each step is evaluated (in the evaluate phase or when `_step_history` is updated with the action result):

```python
last_action = last_step.get("action_id")
cells_changed = last_step.get("n_cells_changed", 0) or 0

if last_action == self._loop_streak_family and cells_changed == 0:
    self._loop_no_change_streak += 1
else:
    # Family changed or board changed — reset
    self._loop_no_change_streak = 1 if cells_changed == 0 else 0
    self._loop_streak_family = last_action if cells_changed == 0 else None
    # Clear blacklist on any successful board change
    if cells_changed > 0:
        self._loop_escaped_blacklist.clear()

if self._loop_no_change_streak >= self._LOOP_ESCAPE_THRESHOLD:
    # Trigger loop escape
    self._loop_escaped_blacklist.add(last_action)
    self._loop_no_change_streak = 0
    self._loop_streak_family = None
    # Also clear any plateau lock on the blacklisted family
    if self._plateau_locked_family in self._loop_escaped_blacklist:
        self._plateau_locked_family = None
    self._trace(
        "solve_loop_escape",
        "loop_policy",
        {"blacklisted_family": last_action, "threshold": self._LOOP_ESCAPE_THRESHOLD},
        f"loop_escape: blacklisting {last_action} — {self._LOOP_ESCAPE_THRESHOLD} consecutive no-change steps",
    )
```

### 3. Apply blacklist in chunk/route selection

In the plateau lock and chunk selection logic (around line 2869 in `solver.py`), before selecting a candidate:

```python
# Filter out loop-blacklisted families
available_for_plateau = [
    a for a in available_actions
    if a not in self._loop_escaped_blacklist
]
if not available_for_plateau:
    # All actions blacklisted — clear and start over (edge case)
    self._loop_escaped_blacklist.clear()
    available_for_plateau = available_actions
```

Use `available_for_plateau` instead of `available_actions` when selecting the plateau candidate or exploration chunk.

### 4. Propagate loop signal into runner replan context

File: `agents/arc3/runner.py`

The `_should_trigger_replan` method (around line 1901) already reads `loop_detected`. Augment it to also check the new blacklist:

```python
blacklist = list(getattr(orchestrator, "_loop_escaped_blacklist", set()))
if blacklist:
    # Signal to the route context that these families are off-limits
    solve_ctx["loop_escape_blacklist"] = blacklist
```

## Concrete File Changes

- `agents/arc3/solver.py`
  - Add 4 new fields in `__init__`.
  - Add streak increment/reset logic after evaluate step.
  - Add loop escape trace emission.
  - Apply blacklist filter in plateau/chunk selection.
- `agents/arc3/runner.py`
  - Propagate `loop_escape_blacklist` into solve context for route phase visibility.
- `tests/test_arc3_solver.py`
  - Add `test_loop_escape_blacklists_repeated_family`:
    - Simulate K steps of same-family zero-change → assert blacklist contains that family.
    - Assert route selection skips blacklisted family.
  - Add `test_loop_escape_clears_on_board_change`:
    - After blacklist established → simulate step with cells_changed > 0 → assert blacklist cleared.

## API/Schema/Test Updates

- API/schema: none.
- Tests: 2 new unit tests.

## Acceptance Criteria

1. After 3 consecutive same-family zero-change steps, that family is added to `_loop_escaped_blacklist`.
2. Route/plateau selection skips blacklisted families.
3. Blacklist clears on any step with `n_cells_changed > 0`.
4. Plateau lock is cleared if the locked family is blacklisted.
5. Trace event "solve_loop_escape" is emitted with blacklisted family name.
6. In smoke run, after repeated ACTION6 stalls, route switches to a different action.

## Validation Commands

```
.venv/bin/python -X dev -m pytest tests/test_arc3_solver.py -q -k loop
.venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1
```

## Risks / Constraints

- If all available actions get blacklisted (unlikely but possible), the safety valve clears the blacklist to avoid a deadlock.
- Threshold K=3 matches the existing `_plateau_lock_family_replan_count` threshold for consistency; make it a single shared constant if desired.
