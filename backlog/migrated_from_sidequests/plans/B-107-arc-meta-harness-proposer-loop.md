# B-107 - ARC Meta-Harness Proposer Loop

## Metadata

- Card: B107
- Priority: P1
- Dependencies: B104, B105, B106

## Summary

Define and later implement the outer-loop proposer that evolves ARC harness candidates using
SideQuests as the experience backend.

## Technical Approach

- Define the proposer’s bounded search policy.
- Specify what information it may retrieve from SideQuests.
- Specify how it hands candidates to the evaluation loop and how it selects the next mutation.

## Concrete File Changes

- Update `benchmarks/arc3/README.md`
- Update `docs/ARCHITECTURE.md`
- Update tracker/card state

## Acceptance Criteria

- The proposer loop is documented as distinct from the ARC harness.
- The proposal/evaluate/store/retrieve/select cycle is explicit.
- The design remains executor-friendly for Gemini/Haiku/Codex style coding agents.

## Validation Commands

- doc review only

## Risks / Constraints

- Keep the proposer bounded; do not let it become an unconstrained autonomous refactor loop.
