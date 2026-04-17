# B-89-arc-prompt-budget-benchmark — ARC Prompt Budget and Retrieval Budget Benchmark

**Card:** B89 | **Priority:** P1 | **Depends on:** B87

## Summary

Add a repeatable measurement harness for ARC prompt strategy so prompt compression, first-input richness, and retrieval-budget changes can be judged on evidence, not intuition.

## Technical Approach

### Metrics
- record prompt token estimate per step
- record total tokens input/output per puzzle
- record invalid-action count
- record no-progress step count
- record runtime seconds
- record first-prompt detail level or a compact proxy for it
- record retrieval payload size and whether it was trigger-based
- record whether the prompt asked for a decision from observed effects

### Comparison Path
- compare the current compressed prompt path against a documented baseline shape
- use puzzle 1 as the first fixed comparison target
- compare a minimal first-input shape against a richer first-input shape so we can see whether more structured initial context improves retrieval quality

### Reporting
- store a compact benchmark note in ARC docs
- ensure the result format is easy to inspect during iterative tuning
- call out whether better first-input detail changed retrieval usefulness or action quality

## Concrete File Changes

- update ARC runner and/or result export with benchmarkable prompt metrics
- document the budget targets and comparison method
- add or update tests around metrics collection

## API/Schema/Test Updates

- no new MCP tools expected
- no schema expansion required unless metrics are persisted structurally
- add focused pytest coverage for any new result fields

## Acceptance Criteria

1. Prompt-budget metrics are emitted for ARC tuning runs
2. Puzzle-1 comparison method is documented
3. Prompt budget targets and retrieval budget targets are written down
4. The benchmark distinguishes between compact and richer first-input shapes
5. Tests validate any added metrics fields

## Validation Commands

- `.venv/bin/pytest -q tests/test_arc3_durable_runner.py`
- `.venv/bin/pytest -q tests/test_arc3_orchestrator.py`

## Notes on Risks or Constraints

- Keep result export readable; do not turn it into an unreadable telemetry dump
- Use token estimates consistently with existing serializer heuristics
