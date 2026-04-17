# B-215 — Plateau Lock Minimum Exploration Gate: Implementation Plan

- **Card:** backlog/B215.md
- **Priority:** P0
- **Dependencies:** B207

## Summary

Prevent plateau lock from engaging before a minimum number of distinct action families have been explored. One action tried is not a plateau.

## Technical Approach

### 1. Add exploration gate constant and tracker

File: `agents/arc3/solver.py`

In `ARCSolveOrchestrator.__init__` (around line 1768):
```python
self._PLATEAU_MIN_EXPLORED: int = 3          # gate: require this many distinct families tried
self._plateau_explored_families: set[str] = set()  # actions tried at least once
```

### 2. Record each executed action family

In the method that processes action execution results (wherever `_step_history` is updated or the executed action is recorded), add:
```python
if executed_action_id:
    self._plateau_explored_families.add(executed_action_id)
```

### 3. Add the gate at plateau lock entry

File: `agents/arc3/solver.py` — plateau lock entry block (around line 2869):

```python
# Gate: plateau lock requires minimum exploration breadth
n_explored = len(self._plateau_explored_families)
min_required = min(self._PLATEAU_MIN_EXPLORED, len(available_actions))
if n_explored < min_required:
    self._trace(
        "solve_plateau_deferred",
        "plateau_policy",
        {
            "step": step,
            "explored": n_explored,
            "required": min_required,
            "explored_families": sorted(self._plateau_explored_families),
        },
        f"plateau deferred: only {n_explored}/{min_required} actions explored",
    )
    # Do NOT set _plateau_locked_family; fall through to exploration chunk
    return  # or continue to exploration selection
```

Place this check before the existing `if self._plateau_locked_family is None:` block.

### 4. Reset explored set on hard replan (optional)

If a full replan clears the solve context, also clear `_plateau_explored_families` so the gate re-applies after a major strategy reset. This prevents old exploration from counting toward a new strategy's gate.

## Concrete File Changes

- `agents/arc3/solver.py`
  - Add `_PLATEAU_MIN_EXPLORED` and `_plateau_explored_families` fields in `__init__`.
  - Record each executed action in `_plateau_explored_families` at execution time.
  - Add exploration gate check before plateau lock entry.

- `tests/test_arc3_solver.py`
  - Add `test_plateau_lock_deferred_until_min_exploration`:
    - Simulate 1 action tried → assert plateau lock is NOT set.
    - Simulate 3 distinct actions tried → assert plateau lock CAN engage.

## API/Schema/Test Updates

- API/schema: none.
- Tests: 1 new unit test.

## Acceptance Criteria

1. With only 1 action tried, plateau lock is never set (trace shows "plateau deferred").
2. With ≥ N distinct actions tried, plateau lock engages normally.
3. `_PLATEAU_MIN_EXPLORED` constant is tunable in one place.
4. Smoke run shows router does not reach "Plateau Exploitation" before ≥ 3 distinct actions appear in ledger.

## Validation Commands

```
.venv/bin/python -X dev -m pytest tests/test_arc3_solver.py -q -k plateau
.venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1
```

## Risks / Constraints

- If the puzzle only has 1 available action, `min(N, len(available_actions))` ensures the gate reduces to 1. The lock will engage after that single action — which is correct.
- Setting N too high delays exploitation unnecessarily on puzzles with few actions; N=3 with the `min()` clamp is safe.
