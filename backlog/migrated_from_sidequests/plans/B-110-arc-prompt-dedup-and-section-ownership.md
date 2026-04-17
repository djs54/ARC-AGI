# B-110 - ARC Prompt Dedup and Section Ownership

## Metadata

- Card: B110
- Priority: P1
- Dependencies: B88, B90, B93, B94

## Summary

Add a final prompt-composition pass that deduplicates repeated facts and assigns clear ownership to
each section of the ARC harness prompt.

## Technical Approach

- audit the current prompt builder in `agents/arc3/orchestrator.py`
- define which facts belong in which section
- add a dedup/compression pass before final prompt assembly
- prefer references to earlier facts over re-stating the same evidence
- reduce or suppress `OBSERVATION` when `OBSERVED EFFECTS` already contains enough board context

## Concrete File Changes

- update `agents/arc3/orchestrator.py`
- update `benchmarks/arc3/PROMPT_STRATEGY.md`
- update `tests/test_arc3_orchestrator.py`

## Section Ownership Rules

- `ACTION FACTS`: operator facts only
- `PATH HYPOTHESES`: path/sequence facts only
- `OBSERVED EFFECTS`: latest transition evidence only
- `SOLVE CONTEXT` and `PLAN`: goal/chunk level only
- `INSTRUCTION`: decision policy only; refer to above sections instead of re-dumping them

## Acceptance Criteria

- card acceptance criteria are implemented and testable
- duplicate fact repetition is materially reduced
- prompt still contains enough information for good action choice

## Validation Commands

- targeted orchestrator tests
- one live puzzle-1 smoke run after implementation

## Risks / Constraints

- do not over-compress away information the policy genuinely needs
- preserve explainability in debug exports even while trimming the actual model prompt
