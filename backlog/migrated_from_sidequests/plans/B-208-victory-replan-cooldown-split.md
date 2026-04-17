# B-208 — Trigger-Specific Victory Inference Cooldowns: Implementation Plan

- **Card:** backlog/B208.md
- **Priority:** P3
- **Dependencies:** B179

## Summary

Split victory inference retry cooldowns so replan-triggered attempts can happen sooner than routine archetype-threshold attempts.

## Technical Approach

### 1. Add separate cooldown trackers

File: `agents/arc3/solver.py`

Add fields:
- `_last_victory_attempt_step` (existing, keep for archetype-threshold path)
- `_last_replan_victory_attempt_step: int = -100` (new)

### 2. Split cooldown checks by trigger

In victory inference trigger logic:
- `archetype_threshold`: retain current longer cooldown window.
- `replan`: evaluate against shorter cooldown window (for example 3 steps).
- `step_fallback` and `zero_progress`: keep existing behavior unless explicitly tied to replan window.

### 3. Update bookkeeping

When an attempt runs:
- Always update global last-attempt.
- Update replan-specific tracker only for replan-triggered attempts.

## Concrete File Changes

- `agents/arc3/solver.py`
  - Add replan-specific tracker field.
  - Refactor trigger gate checks to use per-trigger cooldowns.
- `tests/test_arc3_solver.py`
  - Add test confirming replan attempts are not blocked by global 10-step gate.

## API/Schema/Test Updates

- API/schema: none.
- Tests: one cooldown-specific solver test.

## Acceptance Criteria

1. Replan-triggered victory retry can occur sooner than archetype-threshold retry.
2. Archetype-threshold path still honors conservative cooldown.
3. Test verifies both cooldown lanes.

## Validation Commands

- `.venv/bin/python -X dev -m pytest tests/test_arc3_solver.py -q`
- `.venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1`

## Risks / Constraints

- In mock/no-LLM environments, more frequent retries still may not produce a victory condition; expected behavior.
