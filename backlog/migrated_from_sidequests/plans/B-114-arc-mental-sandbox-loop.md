# B-114 - ARC Mental Sandbox Loop

## Metadata

- Card: B114
- Priority: P0
- Dependencies: B88, B94, B109, B113

## Summary

Add a bounded internal reasoning loop before real ARC move execution so the harness can inspect
its own evidence and guidance without spending environment steps.

## Technical Approach

- introduce a small pre-action loop in the orchestrator
- keep sandbox operations local to harness state
- allow comparison of action facts, path hypotheses, and chunk guidance
- annotate the chosen action with whether sandbox reasoning changed it

## Concrete File Changes

- update `agents/arc3/orchestrator.py`
- update `agents/arc3/solver.py`
- update `benchmarks/arc3/PROMPT_STRATEGY.md`
- update `tests/test_arc3_orchestrator.py`
- update `tests/test_arc3_solver.py`

## Validation Commands

- targeted ARC solver/orchestrator tests
- one live puzzle-1 smoke run after implementation

## Risks / Constraints

- keep the sandbox bounded so it does not explode latency
- sandbox tools must not mutate the real environment
