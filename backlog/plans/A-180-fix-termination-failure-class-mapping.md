# Plan: A180 — Fix `classify_v2_termination` Mislabeling Benign Terminations as `crash`

## Card metadata

- ID: A180
- Priority: P2
- Layer: evaluation/harness
- Dependencies: none

## Summary

`agents/arc4/evaluator.py::classify_v2_termination` maps `WorkflowStatus`/reason strings to `FailureTaxonomy` for the `failure_class` field exported into every run's artifacts. Its mapping dict is missing entries for at least the `"second_veto"` reason (`WorkflowStatus.SKIPPED`) and the `WorkflowStatus.TERMINATED` status itself, so both fall through to the default `FailureTaxonomy.CRASH` even though neither is an actual crash — confirmed live 2026-08-07: a clean 3-step `make smoke` run with no exception anywhere in its logs was labeled `failure_class=crash`.

## Technical approach

1. Read `agents/arc4/workflow.py` in full and enumerate every call site that produces a terminal `WorkflowStatus` + reason pair (`self._finish(...)` calls). As of this card, known call sites:
   - `WorkflowStatus.BUDGET_EXHAUSTED`, `budget_reason` (line ~46)
   - `WorkflowStatus.SKIPPED`, `"second_veto"` (lines ~88, ~118)
   - `WorkflowStatus.STALLED`, `stall_reason` (line ~168)
   - `WorkflowStatus.TERMINATED`, `evaluation_payload.reason or "terminated"` (line ~172)
   - `WorkflowStatus.CRASHED`, ... (line ~178)
   Confirm this list is complete by grepping for `self._finish(` and `WorkflowStatus.` across the file; do not assume the list above is exhaustive without checking.
2. For each status/reason pair found, decide the correct `FailureTaxonomy` bucket:
   - `second_veto` → `FailureTaxonomy.STRATEGY_EXHAUSTED` (same bucket as `stalled`/`stall_detected` — a reasoning failure, not an infrastructure crash)
   - `WorkflowStatus.CRASHED` (any reason) → `FailureTaxonomy.CRASH` (make this explicit in the dict rather than relying on the accidental default)
   - `WorkflowStatus.TERMINATED` with no more specific reason match → do not silently default to `CRASH`; use `FailureTaxonomy.STRATEGY_EXHAUSTED` unless investigation of `evaluation_payload.reason`'s actual values shows a more specific taxonomy fits better. If `evaluation_payload.reason` can carry many distinct meaningful strings, consider whether each needs its own entry rather than one catch-all for `"terminated"`.
   - `WorkflowStatus.SKIPPED` (any other reason besides `second_veto`, if such exist) → investigate and map accurately; do not assume `second_veto` is the only reason this status is ever used with.
   - `WorkflowStatus.RUNNING` — confirm this is never a terminal status (it shouldn't reach `classify_v2_termination` at all); if it can, document why and what it should map to.
3. Update the `mapping` dict in `classify_v2_termination` (`agents/arc4/evaluator.py:320-339`) with the new/corrected entries. Keep the existing fallback (`mapping.get(reason, mapping.get(status, FailureTaxonomy.CRASH.value))`) — the goal is to shrink what falls through to the default, not to remove the safety net for genuinely unrecognized cases.
4. Do not change `agents/common/failure_taxonomy.py::classify_failure` — that is a separate, deliberately-defensive function for a different call path (exception-based classification) and is out of scope for this card.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/evaluator.py` | Extend `classify_v2_termination`'s `mapping` dict with the missing/corrected entries described above |
| `tests/test_a180_termination_classification.py` | New — regression coverage (see Tests below) |

## Tests

New `tests/test_a180_termination_classification.py`:

1. `classify_v2_termination("skipped", "second_veto")` returns `FailureTaxonomy.STRATEGY_EXHAUSTED.value`, not `"crash"`.
2. `classify_v2_termination("crashed", "<any unrecognized reason>")` returns `FailureTaxonomy.CRASH.value` explicitly (not just by accident of the default).
3. `classify_v2_termination("terminated", "<a generic/unrecognized reason>")` does not return `FailureTaxonomy.CRASH.value`.
4. Parametrized test over every `(status, reason)` pair actually producible by `agents/arc4/workflow.py`'s `self._finish(...)` call sites (enumerate from the file, per the audit in step 1 of Technical approach) — assert none of the genuinely-non-crash paths return `CRASH`.
5. Existing passing behavior preserved: `classify_v2_termination("stalled", "stalled")` and `classify_v2_termination("budget_exhausted", ...)` still return their current correct values (regression guard).

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a180_termination_classification.py -v
make test-a
make test-all
```

Live confirmation: `campy status` health check, then `make smoke` (self-capped wait per this session's established methodology), then inspect the tail of `artifacts/agent_execution_trace.json` for the `run_review` entry's `details.puzzle_description` / underlying `failure_class` — confirm a double-veto or generic-terminated run no longer reports `crash`. If the smoke run happens to end cleanly some other way (e.g. runs the full step budget), that's an acceptable outcome for this card — the unit tests are the primary verification; live confirmation is best-effort since termination mode isn't controllable per-run.

## Assumptions/defaults

- `FailureTaxonomy.STRATEGY_EXHAUSTED` is the correct bucket for `second_veto` and generic `terminated` unless the workflow.py audit in step 1 reveals a more specific existing taxonomy value fits better (e.g. if a `TERMINATED` reason string turns out to always indicate goal completion or a specific known condition already covered by `"completed"`/`"won"` → `None`).
- No new `FailureTaxonomy` enum values are needed — the existing set (`agents/common/failure_taxonomy.py`) is assumed sufficient; if the audit finds a termination mode that doesn't fit any existing bucket, flag it in the PR description rather than inventing a new enum value mid-implementation.
