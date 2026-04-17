# B-105 - ARC Meta-Harness Query Surface

## Metadata

- Card: B105
- Priority: P1
- Dependencies: B104

## Summary

Define the small query surface that a coding-agent proposer will use to navigate prior harness
candidates and evaluation results without brute-force filesystem scraping.

## Technical Approach

- Extend ARC benchmark docs with a compact comparison/query layer.
- Define agent-facing commands/helpers for top candidates, candidate diffs, and failure queries.
- Keep the graph viewer optional; this card is about the proposer-facing comparison surface.

## Concrete File Changes

- Update `benchmarks/arc3/model_eval.py`
- Update `benchmarks/arc3/README.md`
- Update `tools/graph_viewer/README.md`

## Query Surface Requirements

- list top candidates by score/runtime/token frontier
- compare two candidates
- list runs by failure cluster
- list runs by regression type

## Acceptance Criteria

- Card acceptance criteria are documented and testable.
- The output stays compact and comparison-first.
- The surface is usable by Gemini/Haiku style coding executors.

## Validation Commands

- targeted unit tests for comparison helpers once implemented

## Risks / Constraints

- Do not turn the optional graph viewer into a required runtime dependency.
