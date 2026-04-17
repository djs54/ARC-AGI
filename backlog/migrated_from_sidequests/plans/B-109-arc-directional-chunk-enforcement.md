# B-109 - ARC Directional Chunk Enforcement

## Metadata

- Card: B109
- Priority: P0
- Dependencies: B95, B101, B103

## Summary

Turn directional chunks from descriptive solve output into an actual execution constraint on action
selection.

## Technical Approach

- connect active directional chunk state more directly to the orchestrator’s action choice
- record chunk-step execution explicitly
- make overrides explicit and bounded
- trigger dissonance/replanning when a directional chunk repeatedly fails

## Concrete File Changes

- update `agents/arc3/solver.py`
- update `agents/arc3/orchestrator.py`
- update `docs/ARCHITECTURE.md`
- update `tests/test_arc3_solver.py`
- update `tests/test_arc3_orchestrator.py`

## Acceptance Criteria

- card acceptance criteria are implemented and testable
- directional chunks materially shape action choice
- chunk execution history is visible in debug/export output
- repeated zero-progress chunk execution triggers replanning signal

## Validation Commands

- targeted ARC solver/orchestrator tests
- one live puzzle-1 smoke run after implementation

## Risks / Constraints

- do not eliminate all useful override behavior
- keep the mechanism explainable in debug output
