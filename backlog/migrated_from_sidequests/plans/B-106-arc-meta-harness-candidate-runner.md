# B-106 - ARC Meta-Harness Candidate Runner

## Metadata

- Card: B106
- Priority: P1
- Dependencies: B104, B105

## Summary

Build the first executable outer-loop evaluation path for ARC harness candidates.

## Technical Approach

- Define a harness candidate artifact and baseline-vs-candidate evaluation contract.
- Keep evaluation outside the proposer itself.
- Persist result bundles with score, budget, lineage, and failure summaries.

## Concrete File Changes

- Update `benchmarks/arc3/harness.py`
- Update `benchmarks/arc3/model_eval.py`
- Update `benchmarks/arc3/README.md`
- Update `tests/test_arc3_durable_runner.py`

## Acceptance Criteria

- A candidate can be evaluated against a fixed search set.
- Baseline-vs-candidate comparison is automatic.
- Result bundles are comparable across runs.

## Validation Commands

- targeted ARC benchmark / durable-runner tests

## Risks / Constraints

- Keep candidate evaluation deterministic enough for comparison.
- Do not require the proposer to run the eval inline.
