# Plan A-033 — Extract registered plans from trace in trajectory plan-adherence scorer

## Card metadata

- **Card:** `backlog/A033.md`
- **Layer:** evaluation/harness
- **Priority:** P2
- **Depends on:** A012, A029

## Summary

`benchmarks/arc3/trajectory_eval.py::TrajectoryEvaluator._score_plan_adherence` only looks at `step_history[*].solve_context.active_chunk.estimated_actions`. When those per-step snapshots are absent but the trajectory trace carries `register_plan` events, the scorer silently returns the neutral fallback (10/20) with reason `"no active chunk plans recorded"`.

A012 planned to "read registered plans from the same source the orchestrator writes them to" (plan line 19). That half of A012 never landed here, and the aspirational test `tests/test_b186_trajectory_plan_adherence.py` has been captured as a failure ever since.

A033 adds a bounded trace-based fallback without changing behavior for traces that already carry per-step plans.

## Implementation approach

### Production change

In `benchmarks/arc3/trajectory_eval.py`:

1. Extend `_score_plan_adherence(self, step_history)` to `_score_plan_adherence(self, step_history, trace=())`.
2. Keep the existing per-step loop intact — it is the authoritative path when present.
3. After the loop, if `planned_steps == 0` and `trace` is non-empty:
   - Union all action ids from `event["result"]["steps"]` across events where `operation == "register_plan"`.
   - If the union is non-empty, iterate `step_history` a second time and count each entry with an `action_id` as a planned step, with a match when the action id is in the union.
4. Keep the existing neutral fallback path (`planned_steps == 0` after both passes → score 10, reason "no active chunk plans recorded").
5. Update the single call site in `evaluate()` (line 47 pre-change) to pass `trace_list`.

This is purely additive: the fallback never fires when the primary loop finds plans, so existing traces score the same.

### Test change

`tests/test_b186_trajectory_plan_adherence.py::test_plan_adherence_extraction_from_trace` also looked up `score.details.get("plan_adherence_details", {})` — that key does not exist. The evaluator uses the dimension name `plan_adherence` (consistent with `action_diversity`, `hypothesis_convergence`, `exploration_efficiency`, `escalation_quality`). Correct the test to use `plan_adherence`.

## Concrete file edits

- `benchmarks/arc3/trajectory_eval.py`
  - Line 47 (evaluate call): add `trace_list` as second argument.
  - Line 329 (`_score_plan_adherence` signature + body): add `trace` parameter, add the trace-fallback block after the primary loop.
- `tests/test_b186_trajectory_plan_adherence.py`
  - Correct the `score.details.get("plan_adherence_details", {})` lookup to `score.details.get("plan_adherence", {})`.

## API / interface changes

`_score_plan_adherence` gains an optional `trace: Sequence[dict] = ()` keyword-compatible second parameter. No public API surface changes.

## Tests to add or run

- `pytest -q tests/test_b186_trajectory_plan_adherence.py` — 1/1 green (the aspirational test).
- `pytest -q tests/test_b186_trajectory_eval.py` — 5/5 green (ensures no regression in the pre-existing trajectory eval suite).
- `pytest -q -k trajectory` — 6/6 green across both files.
- `make test-a` — 18/18 still green.

## Validation commands

```bash
.venv/bin/python -m pytest -v tests/test_b186_trajectory_plan_adherence.py tests/test_b186_trajectory_eval.py
make test-a
```

## Assumptions / defaults

- `register_plan` events in the trace always carry `event["result"]["steps"]` as a list of action-id strings. This matches both the orchestrator's emission shape and the test fixture.
- The per-step authoritative path is correct; the trace fallback is a best-effort measurement for traces that predate per-step plan snapshotting. When both signals are available, we defer to the per-step path.
- The dimension key in `score.details` remains `plan_adherence` for symmetry with the other dimensions.
