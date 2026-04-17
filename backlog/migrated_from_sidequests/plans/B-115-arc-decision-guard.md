# B-115 - ARC Decision Guard

## Metadata

- Card: B115
- Priority: P0
- Dependencies: B109, B112, B114

## Summary

Add a pre-execution decision guard that can block or revise bad ARC moves before the environment
step is spent.

## Technical Approach

- inspect candidate actions against loop history, active chunks, and locked evidence
- return critique/revision feedback before execution
- expose guard decisions in debug output

## Concrete File Changes

- update `agents/arc3/orchestrator.py`
- update `agents/arc3/solver.py`
- update `tests/test_arc3_orchestrator.py`
- update `tests/test_arc3_solver.py`

## Validation Commands

- targeted ARC solver/orchestrator tests
- one live puzzle-1 smoke run after implementation

## Risks / Constraints

- avoid blocking too aggressively
- keep guard logic explainable in debug output
