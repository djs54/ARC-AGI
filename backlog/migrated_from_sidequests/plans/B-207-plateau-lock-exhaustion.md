# B-207 — Plateau Lock Exhaustion Escape: Implementation Plan

- **Card:** backlog/B207.md
- **Priority:** P2
- **Dependencies:** B176, B177

## Summary

Prevent repeated re-locking to the same ineffective action family during plateau mode by adding forced unlock after repeated no-progress replan cycles.

## Technical Approach

### 1. Track lock exhaustion state

File: `agents/arc3/solver.py`

Add fields in solver init:
- `_plateau_lock_family_replan_count: int = 0`
- `_plateau_lock_last_family: Optional[str] = None`

### 2. Increment/reset counters in plateau policy

When plateau mode is active:
- If locked family unchanged and no-progress/replan signals persist, increment counter.
- If family changes or progress appears, reset counter.

### 3. Forced unlock path

If counter reaches threshold (default 3):
- Clear `_plateau_locked_family`.
- Mark current plateau chunk failed with reason `plateau_exhausted`.
- Reset exhaustion counter.
- Emit trace event with forced unlock reason.

### 4. Ensure post-unlock behavior explores

After unlock, chunk selection should flow through exploration/default path unless new evidence supports re-lock.

## Concrete File Changes

- `agents/arc3/solver.py`
  - Add exhaustion state fields.
  - Add increment/reset logic in plateau handling block.
  - Add forced unlock branch and trace emission.
- `tests/test_arc3_solver.py`
  - Add unit test for repeated no-progress plateau escape.

## API/Schema/Test Updates

- API/schema: none.
- Tests: add one plateau-specific unit test.

## Acceptance Criteria

1. Repeated no-progress replans do not keep same lock forever.
2. Forced unlock occurs at threshold and is trace-visible.
3. Solver can re-enter exploration after forced unlock.

## Validation Commands

- `.venv/bin/python -X dev -m pytest tests/test_arc3_solver.py -q`
- `.venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1`

## Risks / Constraints

- Too-low threshold may unlock prematurely; threshold should be constant and easy to tune.
