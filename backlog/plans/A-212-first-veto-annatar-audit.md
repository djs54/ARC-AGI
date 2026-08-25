# Plan: A212 — Audit: Should the First Vet Rejection Report to Annatar?

## Card metadata

- ID: A212
- Priority: P2
- Layer: ARC runtime
- Dependencies: A207, A210

## Summary

Investigation-first card. Settle whether a single (first) vet rejection — currently fully absorbed by a same-cycle local replan loop, never reaching Annatar — represents a real accountability gap, or whether the current design is correctly scoped per Shift B's "bounded sub-agent" principle. Do not presuppose the answer.

## Technical approach

### 1. Re-read the primary sources

- `ARCHITECTURE.md`'s Shift B text — specifically the distinction between "short-lived sub-agents... return raw results, never independent conclusions" (bounded, no escalation needed) and "a single primary agent owns end-to-end reasoning" (accountability). A single veto-then-local-retry is arguably exactly the bounded case; confirm or refute this against the actual text, not a paraphrase.
- `backlog/A207.md`'s Outcome section — re-read A207's actual stated reasoning for why the SECOND veto needed Annatar's involvement. Was the key factor "a veto happened" (which would argue for reporting on the first one too) or "the episode is about to end" (which would argue the first veto genuinely doesn't need it, since it isn't ending anything)? Get this distinction right — it's the crux of the whole audit.
- `agents/arc4/workflow.py`'s current veto-handling code (both the first-veto local-replan block and the `_route_second_veto_through_annatar` call) — confirm the exact current structure before reasoning about it.

### 2. Consider three possible conclusions, pick the one the evidence actually supports

- **No change needed.** The first veto is bounded, local, low-stakes (a same-cycle retry, not an episode-ending event) — exactly the kind of thing Shift B says shouldn't need to escalate. Write the specific reasoning for why this is different in kind from `second_veto`, not just different in degree.
- **Visibility only (likely middle ground).** Fold the first veto's reason/alternative into `CycleSignals` the next time Annatar is actually invoked (e.g. add a field like `first_veto_reason: str | None` alongside the existing `stall_reason` kwarg, read from `state.latest_veto_reason` which is already being written) — Annatar gets informed without gaining decision authority over the local replan itself. This mirrors A209's `check_budget` pattern (informed, not empowered) rather than A207's `second_veto` pattern (full decision authority) — if this is the conclusion, explain why THIS gap is more like `check_budget`'s than `second_veto`'s.
- **Full escalation**, mirroring `_route_second_veto_through_annatar` exactly. Only choose this if the evidence genuinely supports treating a first veto with the same stakes as a second one — be skeptical of this option by default, since escalating routine, usually-successful local retries risks exactly the "noise instead of signal" failure mode the card's Problem section warns about.

### 3. Implement only what the audit concludes, nothing more

If a fix lands, keep it the smallest version that closes the actual gap identified. If "visibility only," this should be a small, additive change (a new `CycleSignals`/kwarg field, populated from data that's already being written to `state`) — not a new control-flow branch, not a new `_route_*_through_annatar` method.

## Concrete file changes

| File | Change |
|------|--------|
| `backlog/A212.md` | Outcome section documents the audit's finding either way |
| `agents/arc4/workflow.py` / `agents/arc4/annatar_signals.py` (only if a fix lands) | Depends entirely on which of the three conclusions above the audit reaches |
| `tests/test_a212_*.py` (new, only if a fix lands) | Coverage matching whichever conclusion was reached |
| `docs/annatar-accountability-map.md` | Update the `vet` touchpoint's row with the audit's conclusion |

## Tests

Only applicable if a fix lands — shape entirely depends on which conclusion (see Technical approach) the audit reaches. If "visibility only": a test confirming the first veto's reason reaches `CycleSignals` on the next Annatar invocation, and a regression test confirming the local replan's own behavior is completely unchanged (visibility must not alter control flow). If "full escalation": mirror A207's own test suite shape (`TestSecondVetoRoutesThroughReasoner`, now presumably renamed) exactly, adapted for the first-veto call site.

## Validation commands

```bash
# If a fix lands:
.venv/bin/python -m pytest tests/test_a212_*.py -v
make test-a
make test-all
# If no fix lands:
make test-a
make test-all
```

## Assumptions/defaults

- This plan deliberately does not pre-decide the outcome, same as A209's approach. If your own reading of the primary sources produces a conclusion different from the three sketched above, that's an acceptable outcome — document the actual reasoning.
