# B-104 - ARC Meta-Harness Experience Store

## Metadata

- Card: B104
- Priority: P0
- Dependencies: B89, B90, B95

## Summary

Define the graph-native experience-store contract that lets SideQuests support ARC harness
evolution as an outer-loop memory substrate.

## Technical Approach

- Document the ARC Harness / Meta-Harness / SideQuests split in architecture docs.
- Define the outer-loop entities and relationships needed for harness evolution.
- Specify the retrieval questions the meta-harness must be able to answer quickly.
- Keep this doc-level and contract-level first; no new runtime behavior required yet.

## Concrete File Changes

- Update `docs/ARCHITECTURE.md`
- Update `benchmarks/arc3/README.md`
- Update `docs/retrieval-contract.md`
- Update backlog tracker/card state

## Required Experience Entities

- `HarnessCandidate`
- `HarnessEvalRun`
- `HarnessMutation`
- `HarnessScoreSummary`
- `HarnessFailureCluster`
- `PuzzleTraceRef`

## Required Retrieval Questions

1. Which harness candidates improved score without blowing the token/runtime budget?
2. Which mutations repeatedly caused the same failure mode?
3. Which prior candidates performed best on puzzles with a similar failure signature?
4. Which traces promoted action facts or path hypotheses that later correlated with success?
5. Which regressions are linked to retrieval policy changes versus solve-policy changes?

## Acceptance Criteria

- Card acceptance criteria are reflected in the docs.
- The inner-loop vs outer-loop memory boundary is explicit.
- The graph model is relationship-first and comparison-oriented.

## Validation Commands

- doc review only

## Risks / Constraints

- Do not let this collapse SideQuests into “the ARC harness.”
- Keep the design graph-native but operationally small enough to implement in later cards.
