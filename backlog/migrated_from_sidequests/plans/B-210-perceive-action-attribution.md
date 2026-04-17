# B-210 — Per-Step PERCEIVE Action Attribution Fix: Implementation Plan

- **Card:** backlog/B210.md
- **Priority:** P2
- **Dependencies:** B202, B205

## Summary

Fix perceive question/action attribution so each `evaluate->perceive` summary references the actual action executed for that same step.

## Technical Approach

### 1. Canonical action plumbing

File: `agents/arc3/runner.py`

- Track the executed action id from the step result object as canonical.
- Pass this canonical `action_id` directly into `perceive_step_response()`.
- Avoid deriving from mutable/stale `_step_history[-1]` if there is any ambiguity.

### 2. Defensive usage in orchestrator

File: `agents/arc3/orchestrator.py`

- In `perceive_step_response()`, prefer explicit parameter action_id.
- Only fallback to history when parameter is missing.
- Add mismatch trace event when supplied action conflicts with latest recorded step action.

### 3. Tests for non-default actions

Files: `tests/test_arc3_orchestrator.py`, `tests/test_arc3_durable_runner.py`

- Add case where step action is `ACTION6` and assert perceive question includes `ACTION6`.
- Add integration check: for each evaluate->perceive transition, perceived action matches adjacent step snapshot action.

## Concrete File Changes

- `agents/arc3/runner.py`
  - Source and pass canonical action id to per-step perceive.
- `agents/arc3/orchestrator.py`
  - Tighten action_id sourcing and add mismatch trace.
- `tests/test_arc3_orchestrator.py`
  - Add unit test for action attribution correctness.
- `tests/test_arc3_durable_runner.py`
  - Add integration assertion on step/perceive alignment.

## Acceptance Criteria

1. Perceive question action matches the step action for the same step.
2. No stale prior action appears in per-step perceive summaries.
3. Mismatch detection trace exists for debugging.
4. Added tests pass.
5. Live smoke output confirms action alignment.

## Validation Commands

- `.venv/bin/python -X dev -m pytest tests/test_arc3_orchestrator.py -q`
- `.venv/bin/python -X dev -m pytest tests/test_arc3_durable_runner.py -q`
- `.venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1`

## Risks / Constraints

- If step snapshots are delayed/missing in rare flows, strict matching may need guarded fallback.
- Keep behavior additive; do not alter phase sequencing.