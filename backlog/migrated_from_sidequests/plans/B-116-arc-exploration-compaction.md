# B-116 - ARC Exploration Compaction

## Metadata

- Card: B116
- Priority: P1
- Dependencies: B88, B90, B93, B94

## Summary

Add a structured exploration compaction artifact so the harness preserves long-run exploration
knowledge without carrying large raw histories.

## Technical Approach

- summarize older exploration state into a compact structure
- keep action facts, loop failures, and surviving hypotheses
- consume the artifact in later prompts and decisions

## Concrete File Changes

- update `agents/arc3/hypothesis.py`
- update `agents/arc3/orchestrator.py`
- update `benchmarks/arc3/PROMPT_STRATEGY.md`
- update `tests/test_arc3_hypothesis.py`
- update `tests/test_arc3_orchestrator.py`

## Validation Commands

- targeted ARC hypothesis/orchestrator tests
- one live puzzle-1 smoke run after implementation

## Risks / Constraints

- compaction must preserve decision-useful facts, not only narrative text
