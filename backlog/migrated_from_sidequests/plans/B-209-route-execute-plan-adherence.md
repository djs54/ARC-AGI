# B-209 — Route→Execute Plan Adherence and Provenance: Implementation Plan

- **Card:** backlog/B209.md
- **Priority:** P1
- **Dependencies:** B203, B205

## Summary

Add a strict but safe contract between route-selected chunk intent and execute action choice. Silent drift is converted into either aligned behavior or explicit override with provenance.

## Technical Approach

### 1. Expose expected action from solver context

File: `agents/arc3/solver.py`

- Ensure solve context includes canonical route expectation:
  - `expected_action`
  - `expected_action_family`
  - `active_chunk_source`
- Prefer first `active_chunk.estimated_actions` element when available.

### 2. Enforce in execute policy

File: `agents/arc3/orchestrator.py`

- In action policy enforcement path, compare proposed `action_id` against `expected_action`.
- If mismatch:
  - If no valid override reason, rewrite to expected action.
  - If valid override reason exists, keep action and stamp:
    - `override_reason`
    - `expected_action`
    - `selected_action`
    - `adherence_ok=False`
- If match, stamp `adherence_ok=True`.

### 3. Surface diagnostics in orchestration report

File: `agents/arc3/runner.py`

- Add planner/executor adherence counters and mismatch examples.
- Distinguish this from phase/tool legality checks.

## Concrete File Changes

- `agents/arc3/solver.py`
  - Add/normalize expected-action fields in returned solve context.
- `agents/arc3/orchestrator.py`
  - Update `_enforce_action_policy` (or equivalent) to apply adherence contract.
- `agents/arc3/runner.py`
  - Extend orchestration report with adherence diagnostics.
- `tests/test_arc3_orchestrator.py`
  - Add tests: exact match path, forced alignment path, explicit override path.
- `tests/test_arc3_durable_runner.py`
  - Add report assertions for mismatch accounting.

## Acceptance Criteria

1. Route expectation and execute action are aligned by default.
2. Mismatch without override reason is corrected to expected action.
3. Mismatch with override reason is retained and fully annotated.
4. Orchestration diagnostics include adherence mismatch counts.
5. Added tests pass.

## Validation Commands

- `.venv/bin/python -X dev -m pytest tests/test_arc3_orchestrator.py -q`
- `.venv/bin/python -X dev -m pytest tests/test_arc3_durable_runner.py -q`
- `.venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1`

## Risks / Constraints

- Over-constraining execute could block useful exploratory pivots; mitigated by explicit override lanes.
- Report semantics must remain backward compatible for existing consumers.