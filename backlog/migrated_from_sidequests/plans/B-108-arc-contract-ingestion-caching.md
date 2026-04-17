# B-108 - ARC Contract Ingestion Caching

## Metadata

- Card: B108
- Priority: P0
- Dependencies: B87, B89

## Summary

Reduce repeated SideQuests overhead on stable ARC protocol concepts so live ARC runs stop paying
full ingestion cost for the same contract knowledge every time.

## Technical Approach

- identify the stable ARC protocol concepts currently being re-ingested
- pre-seed or cache their gist/schema classifications
- bypass repeated relation extraction for normalized ARC contract fragments
- keep puzzle-specific observations and promoted facts on the normal write path

## Concrete File Changes

- update `agents/arc3/api_knowledge.py`
- update `run_single_puzzle.py`
- update `docs/ARCHITECTURE.md`
- update `benchmarks/arc3/README.md`
- add/update `tests/test_arc3_durable_runner.py`

## Acceptance Criteria

- card acceptance criteria are implemented and testable
- repeated ARC protocol ingestion cost is reduced
- puzzle-specific writes are preserved

## Validation Commands

- targeted ARC durable-runner tests
- one live smoke comparison if feasible

## Risks / Constraints

- do not suppress puzzle-specific cognition by accident
- keep the optimization bounded to stable ARC protocol concepts
