# B-206 — Prevent Archetype Regression to UNKNOWN: Implementation Plan

- **Card:** backlog/B206.md
- **Priority:** P2
- **Dependencies:** B205

## Summary

Stabilize early-step archetype identity by preventing transient fallback to `unknown` when the current archetype has already reached usable confidence.

## Technical Approach

### 1. Add non-regression guard in `SolveEngine.solve()`

File: `agents/arc3/solver.py`

Location: archetype update block after `archetype, confidence = self.archetype_classifier.update(...)`.

Algorithm:
- Read prior `(self._archetype, self._archetype_confidence)`.
- If new archetype is `UNKNOWN` and prior archetype is non-unknown with confidence >= 0.25:
  - Keep prior archetype.
  - Set confidence to `max(prior_conf - 0.05, 0.25)`.
- Preserve existing B148 logic for same-archetype confidence smoothing.

### 2. Add regression test

File: `tests/test_arc3_solver.py`

Test case:
- Seed solver state with archetype `SPACE` at confidence `0.40`.
- Mock classifier update to return `UNKNOWN, 0.10`.
- Run one solve step.
- Assert returned solve context archetype remains `SPACE` with confidence in `[0.25, 0.40)`.

## Concrete File Changes

- `agents/arc3/solver.py`
  - Add guard branch in archetype assignment path.
- `tests/test_arc3_solver.py`
  - Add unit test for unknown-regression suppression.

## API/Schema/Test Updates

- API/schema: none.
- Tests: add one focused solver unit test; run related solver test module.

## Acceptance Criteria

1. Previously grounded archetype is not replaced by transient `unknown` output.
2. Confidence decays modestly when guard applies.
3. Existing archetype pivot behavior remains unchanged.

## Validation Commands

- `.venv/bin/python -X dev -m pytest tests/test_arc3_solver.py -q`
- `.venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1`

## Risks / Constraints

- Overly aggressive hold may delay legitimate unknown transitions; mitigated by confidence floor and decay.
