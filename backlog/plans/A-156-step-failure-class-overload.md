# Plan: A156 — Per-Step Telemetry Overloads `failure_class` With Non-Taxonomy Decision Labels

## Context

`agents/arc4/telemetry.py::_step_snapshot` (current lines 133-200) builds the per-step live.jsonl row. Lines 189-197:

```python
if evaluation is not None:
    snapshot.update(
        {
            "decision": evaluation.decision.value,
            "falsification_delta": evaluation.falsification_delta,
            "failure_class": self._failure_class_from_decision(evaluation.decision),
            "failure_reason": evaluation.reason,
        }
    )
```

`_failure_class_from_decision` (current lines 312-318) maps `WorkflowDecision.PIVOT` → `"pivot"` and `WorkflowDecision.TERMINATE` → `"terminate"` — neither of which is a member of `agents.common.failure_taxonomy.FailureTaxonomy` (confirmed: 12 members, none named `pivot` or `terminate`). This corrupts the per-step `failure_class` field on every intermediate step where `repeated_falsification` triggers a PIVOT decision, even though the run is healthy and continuing (live evidence: game `tu93-0768757b`, steps 5-8 all show `failure_class: pivot` while the run continued to step 8 of a 10-step budget and only the *final* row correctly shows `failure_class: strategy_exhausted`).

Confirmed via grep: `_failure_class_from_decision` has exactly one call site (this one). No consumer reads the per-step `failure_class` key via bracket indexing (`snapshot["failure_class"]`) — all downstream reads use `.get("failure_class")` with implicit `None` defaults, so dropping the key entirely is safe (no `KeyError` risk).

## Implementation Steps

### Step 1: Stop writing decision labels into per-step `failure_class`

In `agents/arc4/telemetry.py`, change the `snapshot.update(...)` block (current lines 189-197) to drop the `failure_class` key:

```python
if evaluation is not None:
    snapshot.update(
        {
            "decision": evaluation.decision.value,
            "falsification_delta": evaluation.falsification_delta,
            "failure_reason": evaluation.reason,
        }
    )
```

`decision` (already present, correctly typed as the `WorkflowDecision` value string) remains the single source of truth for per-step decision state. `failure_reason` is left as-is — unlike `failure_class`, it's a free-form descriptive string (e.g. `"repeated_falsification"`, `"prediction_confirmed_without_progress"`) that doesn't claim membership in a fixed taxonomy, so it isn't misleading the same way.

### Step 2: Remove the now-dead `_failure_class_from_decision`

Delete the method (current lines 312-318) entirely — Step 1 removes its only call site.

### Step 3: Confirm no other consumer needs the per-step value

```bash
grep -rn 'snapshot.get("failure_class")\|snapshot\["failure_class"\]' --include="*.py" . | grep -v __pycache__ | grep -v archive/
```

The one hit outside `agents/arc4/telemetry.py` itself is `benchmarks/arc3/world_model_eval.py`'s `WorldModelEvaluator.build_decision_row` (or similarly named method) — read enough of that function to confirm it's building a row shaped for a *different* snapshot kind (`world_model_decision`, with fields like `trigger`/`decision_step`/`stall_evidence_count` that don't appear in arc4's v2 "step" snapshot at all) — i.e. it's not actually fed arc4's per-step rows in the current pipeline, so no change needed there. If investigation shows otherwise (it *is* fed v2 step rows and specifically depends on getting `"pivot"`/`"terminate"` back), note that as a scope adjustment and leave a `None`-safe default there instead of assuming it's unaffected.

### Step 4: Tests

New file `tests/test_a156_step_failure_class_overload.py`. Read `agents/arc4/telemetry.py`'s `ArcV2Telemetry`/`_step_snapshot` construction to find how it's normally invoked (likely via `wrap_phase`-wrapped phase calls, or by calling `_step_snapshot` more directly with constructed args — check existing telemetry tests, e.g. `tests/test_a078_world_model_evaluation_harness.py` was in the v1-era archived set, so look for whatever arc4-era telemetry tests remain, e.g. grep `tests/` for `ArcV2Telemetry` usage as a pattern reference).

1. `test_step_snapshot_pivot_decision_does_not_set_failure_class` — construct an `EvaluationResult`-shaped object (or use the real `EvaluationResult` dataclass) with `decision=WorkflowDecision.PIVOT`, feed it through the snapshot-building path, assert the resulting dict has no `"failure_class"` key (or the key is absent/`None` — match whatever Step 1 actually produces) while `"decision"` is `"pivot"`.
2. `test_step_snapshot_terminate_decision_does_not_set_failure_class` — same shape for `WorkflowDecision.TERMINATE`.
3. `test_step_snapshot_continue_decision_unaffected` — `WorkflowDecision.CONTINUE` baseline, confirm no regression (this case never set `failure_class` to a non-None value before either, per `_failure_class_from_decision`'s `return None` default branch — just confirms nothing else broke).
4. `test_final_result_failure_class_still_uses_taxonomy` — confirm the *separate* final-result build path (`classify_v2_termination`, current line ~72 in `telemetry.py`) is untouched and still produces real `FailureTaxonomy` values — this path doesn't go through `_failure_class_from_decision` at all, so this is mostly a regression guard proving the two paths are genuinely independent.

## Verify

```bash
.venv/bin/python -m pytest tests/test_a156_step_failure_class_overload.py -v
make test-a
make test-all
```

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/telemetry.py` | Drop `failure_class` from per-step snapshot `update()`; remove dead `_failure_class_from_decision` |
| `tests/test_a156_step_failure_class_overload.py` | New, 4 tests |

## Risks

- Very low — subtractive fix (removes a field/method), no new logic surface. The only real risk is an undiscovered consumer depending on the corrupted per-step value, which Step 3's investigation is meant to catch before landing.
