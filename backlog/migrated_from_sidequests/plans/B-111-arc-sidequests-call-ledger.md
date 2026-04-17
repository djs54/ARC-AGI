# B-111 - ARC SideQuests Call Ledger in Debug Export

## Metadata

- Card: B111
- Priority: P0
- Dependencies: B92, B108

## Summary

Add a compact SideQuests call ledger to ARC debug exports so each live run shows which
SideQuests interactions occurred, how expensive they were, and what they returned.

## Technical Approach

- identify the main ARC-facing SideQuests call sites:
  - retrieval reads
  - hypothesis/plan/lesson writes
  - bootstrap contract seeding
  - finalization writes
- define a compact ledger event shape shared across phases
- record ledger entries during the run without bloating the prompt itself
- attach the aggregated ledger to the debug export in `submission_results_single.json`
- keep summaries human-readable and bounded

## Concrete File Changes

- update `benchmarks/arc3/adapter.py`
- update `agents/arc3/orchestrator.py`
- update `agents/arc3/runner.py`
- update `tests/test_arc3_durable_runner.py`
- update `tests/test_arc3_orchestrator.py`

## Ledger Fields

- `step`: integer step number or `0`/`final`
- `phase`: `bootstrap`, `perceive`, `hypothesize`, `solve`, `act`, `evaluate`, `finalization`
- `call_type`: compact operation label such as `recall`, `write_trace`, `lesson_distill`, `plan_register`
- `mode`: `read` or `write`
- `input_summary`: bounded summary of the query or payload
- `result_summary`: bounded summary of what came back or what was written
- `latency_ms`: elapsed time
- `decision_used`: optional boolean or short note when the result affected the next action

## Acceptance Criteria

- card acceptance criteria are implemented and testable
- exported ledger is readable and compact
- at least one live smoke run demonstrates the value of the ledger

## Validation Commands

- targeted ARC orchestrator/runner tests
- one live puzzle-1 smoke run after implementation

## Risks / Constraints

- do not dump raw full payloads into the ledger
- avoid double-logging the same event in both write traces and the ledger
- keep the ledger for debugging/export only, not prompt context
