# B-117 - ARC Typed Decision Packets

## Metadata

- Card: B117
- Priority: P1
- Dependencies: B110, B114, B116

## Summary

Move ARC prompt construction onto typed decision packets so structured runtime logic no longer has
to operate on loosely-assembled text.

## Technical Approach

- define packet/block structures for the key decision surfaces
- construct packets first, then render prompt text from them
- keep debug/export visibility aligned with the packet model

## Concrete File Changes

- update `agents/arc3/orchestrator.py`
- update `agents/arc3/hypothesis.py`
- update `benchmarks/arc3/PROMPT_STRATEGY.md`
- update `tests/test_arc3_orchestrator.py`

## Validation Commands

- targeted ARC orchestrator tests
- one live puzzle-1 smoke run after implementation

## Risks / Constraints

- do not let the packet layer become pure ceremony; it must simplify real runtime logic
