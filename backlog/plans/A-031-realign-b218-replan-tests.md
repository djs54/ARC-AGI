# Plan A-031 — Realign B218 REPLAN-branching tests with A017 tuple return

## Card metadata

- **Card:** `backlog/A031.md`
- **Layer:** evaluation/harness (test-only)
- **Priority:** P2
- **Depends on:** A017, A029

## Summary

`tests/test_b218_replan_branching.py` drifted out of sync with A017, which restored runner-side evidence-aware REPLAN branching. The production contract changed in two ways that this test file did not track:

1. `DurableARCRunner._replan_target` now returns `tuple[SolvePhase, str]` — `(target_phase, route_reason)`.
2. The `replan_exit` trace event is emitted by `DurableARCRunner._record_phase_transition` metadata, not by `_replan_target`. The only trace event `_replan_target` emits directly is `replan_escalation`, on the repeat-signature path.

The tests still asserted a bare `SolvePhase` return and a `_emit_trace_event("replan_exit", "route", {...})` call that production never makes. This card realigns the assertions without touching production.

## Implementation approach

For each of the five tests in `tests/test_b218_replan_branching.py`:

1. Replace

   ```python
   target = runner._replan_target(orchestrator)
   assert target == SolvePhase.X
   orchestrator._emit_trace_event.assert_called_with(
       "replan_exit", "route", {"target": "...", "route_reason": "..."}
   )
   ```

   with

   ```python
   target, reason = runner._replan_target(orchestrator)
   assert target == SolvePhase.X
   assert reason == "..."
   ```

2. For `test_replan_branch_signature_escalation`, keep the escalation trace assertion but target the correct event name. `_replan_target` emits `replan_escalation` (not `replan_exit`) on the repeat-signature path, so assert that exactly one `replan_escalation` call was made after the second invocation.

## Concrete file edits

- `tests/test_b218_replan_branching.py` — five assertion blocks updated as above.

No production files touched.

## API / interface changes

None.

## Tests to add or run

- `pytest -q tests/test_b218_replan_branching.py` — must be 5/5 green.
- `make test-a` — must remain 18/18 green (no regression in the A-series baseline).

## Validation commands

```bash
python3 -m pytest tests/test_b218_replan_branching.py -v
make test-a
```

## Assumptions / defaults

- The A017 contract (`_replan_target -> tuple[SolvePhase, str]` with emit-site moved to `_record_phase_transition`) is the intended production behavior going forward. There is no plan to re-emit `replan_exit` from `_replan_target` directly.
- `replan_escalation` remains the correct trace event for the repeat-signature path.
