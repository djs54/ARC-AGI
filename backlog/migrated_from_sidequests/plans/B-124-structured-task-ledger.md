# B-124 Plan - Structured Task Ledger

Card: B124
Priority: P1
Dependencies: none

## Summary
Add a structured chunk ledger to SolveContext so ARC tracks active/pending/completed/failed chunk work across steps and persists it in checkpoints.

## Ecosystem / Ownership
- Layer: Agent Runtime & Harness (`agents/arc3/`).
- Checkpoint serialization in Control Plane file `agents/arc3/checkpoint.py`.
- No SideQuests cross-import changes.

## Technical Approach
1. In `agents/arc3/solver.py`, define `ChunkLedgerEntry` dataclass:
- `description: str`
- `status: str` (pending|active|completed|failed)
- `steps_used: int`
- `outcome_summary: str`
2. Add `chunk_ledger: list[ChunkLedgerEntry]` to `SolveContext`.
3. Update chunk lifecycle paths to transition statuses deterministically:
- new chunk chosen -> active
- chunk consumed with progress -> completed
- chunk exhausted/stale without progress -> failed
- planned but not active -> pending
4. Update solve section rendering to include compact ledger lines; cap to 8 entries.
5. In `agents/arc3/checkpoint.py`, include `chunk_ledger` in save/load structures with backward-compatible defaults.

## Concrete File Changes
- Modify: `agents/arc3/solver.py`
- Modify: `agents/arc3/checkpoint.py`
- Modify/Create tests: `tests/test_arc3_solver.py` and/or `tests/test_b124_task_ledger.py`

## Test Plan
- `pytest -q tests/test_arc3_solver.py tests/test_arc3_orchestrator.py`
- `pytest -q tests/test_b124_task_ledger.py` (if created)

## Acceptance Criteria Mapping
- Ledger status coverage: tests for completed/active/pending/failed.
- Cap of 8 entries: test pruning oldest completed entries first.
- Checkpoint roundtrip: save/load preserves ledger values.
- No behavior regressions outside solve section enrichment.

## Risks / Constraints
- Keep prompt token growth bounded via compact rendering.
- Maintain compatibility with existing checkpoint files lacking ledger field.
