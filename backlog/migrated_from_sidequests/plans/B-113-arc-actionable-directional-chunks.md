# B-113 - ARC Actionable Directional Chunks

## Metadata

- Card: B113
- Priority: P0
- Dependencies: B109, B112

## Summary

Make directional chunks produce and retain actionable step sequences early enough to meaningfully
guide ARC action selection.

## Technical Approach

- inspect where directional chunks are generated, consumed, and emptied
- ensure directional chunks keep usable `estimated_actions` across the guided phase
- avoid premature exhaustion that leaves only a summary without actionable guidance
- align solver/orchestrator chunk lifecycle so the export reflects real guidance, not empty shells

## Concrete File Changes

- update `agents/arc3/solver.py`
- update `agents/arc3/orchestrator.py`
- update `tests/test_arc3_solver.py`
- update `tests/test_arc3_orchestrator.py`

## Acceptance Criteria

- card acceptance criteria are implemented and testable
- directional chunks remain actionable during the steps they are intended to guide
- chunk lifecycle remains visible and explainable in debug export

## Validation Commands

- targeted ARC solver/orchestrator tests
- one live puzzle-1 smoke run after implementation

## Risks / Constraints

- do not force directional chunk persistence so hard that dissonance/replanning stops working
- preserve accurate export/debug visibility of chunk state transitions
