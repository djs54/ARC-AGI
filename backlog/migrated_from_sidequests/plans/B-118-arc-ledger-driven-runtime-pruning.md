# B-118 - ARC Ledger-Driven Runtime Pruning

## Metadata

- Card: B118
- Priority: P1
- Dependencies: B111, B108

## Summary

Use the new SideQuests call ledger to prune expensive low-value runtime behavior inside the ARC
harness.

## Technical Approach

- analyze per-phase ledger patterns
- identify calls with high latency and low observed value
- suppress or down-rank those calls in later decisions
- expose pruning decisions in export/debug output

## Concrete File Changes

- update `agents/arc3/orchestrator.py`
- update `agents/arc3/runner.py`
- update `benchmarks/arc3/PROMPT_STRATEGY.md`
- update `tests/test_arc3_orchestrator.py`
- update `tests/test_arc3_durable_runner.py`

## Validation Commands

- targeted ARC orchestrator/runner tests
- one live puzzle-1 smoke run after implementation

## Risks / Constraints

- do not prune calls that are expensive but genuinely decision-critical
