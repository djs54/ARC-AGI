# B-112 - ARC Valid Chunk Enforcement Gate

## Metadata

- Card: B112
- Priority: P0
- Dependencies: B109

## Summary

Tighten ARC chunk enforcement so only active chunks that are both guidance-grade and still valid
for the current action set can override action selection.

## Technical Approach

- audit the chunk-enforcement gate in `agents/arc3/orchestrator.py`
- distinguish chunk sources that are eligible for hard guidance from exploratory chunks
- require at least one valid remaining action before enforcement
- align stale-chunk detection in `agents/arc3/solver.py` with the orchestrator gate
- ensure rationale text names the actual enforced chunk type/source

## Concrete File Changes

- update `agents/arc3/orchestrator.py`
- update `agents/arc3/solver.py`
- update `tests/test_arc3_orchestrator.py`
- update `tests/test_arc3_solver.py`

## Acceptance Criteria

- card acceptance criteria are implemented and testable
- exhausted explore chunks do not produce policy overrides
- invalid/stale chunks are not enforced
- rationale text accurately reflects the enforced chunk source

## Validation Commands

- targeted ARC solver/orchestrator tests
- one live puzzle-1 smoke run after implementation

## Risks / Constraints

- do not over-tighten the gate such that legitimate directional guidance stops working
- preserve explainability in the debug export
