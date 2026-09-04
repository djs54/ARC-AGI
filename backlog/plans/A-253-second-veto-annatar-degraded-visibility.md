# A253 — Second-Veto `annatar_degraded` Visibility: Plan

## Card metadata

- Card: `backlog/A253.md`
- Depends on: A205 (established `annatar_degraded`), A206 (built the call site missing it), A237/A244/A251 (the same discipline extended elsewhere)

## Design (confirmed by direct read before writing this plan)

- `agents/arc4/workflow.py:707-763` — `_route_second_veto_through_annatar`, full function.
- `agents/arc4/workflow.py:258-266` (probe path) and `:556-584` (normal cycle) — the two existing correct call sites, exact placement: `outcome = self._dependencies.annatar(...)` immediately followed by `state.annatar_degraded = outcome.degraded`, before any branching on `outcome`.
- Confirm exact current line numbers before editing (this file has been touched by nearly every recent card).

### The fix

```python
outcome = self._dependencies.annatar(
    state,
    perception_payload,
    synthetic_execution,
    synthetic_evaluation,
    stall_reason="second_veto",
)
# A253: mirrors the probe-path/normal-cycle call sites' own placement --
# set immediately after the call, before any branching on the result.
# Previously omitted here (this call site was added in A206, the same day
# A205 established this field, and apparently never backfilled). See
# backlog/A253.md.
state.annatar_degraded = outcome.degraded
if outcome.resume_mapping:
    ...
```

(Illustrative — confirm the exact current code shape before editing; insert the new line directly after the `outcome = self._dependencies.annatar(...)` call, before the `if outcome.resume_mapping:` branch.)

## Implementation approach

### Files

- Modify: `agents/arc4/workflow.py` — `_route_second_veto_through_annatar`.
- Test: new `tests/test_a253_second_veto_annatar_degraded.py`.

### TDD

- New test: a mocked/faked `annatar` dependency returning `AnnatarOutcome(decision="advance", degraded=True, ...)` when called via the second-veto path — confirm `state.annatar_degraded is True` after the call.
- New test: same shape with `degraded=False` — confirm `state.annatar_degraded is False`, the regression guard proving the new line reads the real value rather than hardcoding `True`.
- New test: confirm this doesn't change any of `_route_second_veto_through_annatar`'s existing decision-handling behavior — run the exact scenarios `TestSecondVetoRoutesThroughAnnatar` (or whichever is the real existing test class name, confirm via grep) already covers and confirm identical outcomes before/after this one-line addition.
- Regression: existing second-veto tests unchanged.

### Validation commands

```bash
.venv/bin/python -m pytest tests/test_a253_second_veto_annatar_degraded.py -v
.venv/bin/python -m pytest tests/test_a202_annatar_orchestrator_integration.py -v -k "veto"
make test-a
make test-all
```

### Live-verify

Same environment/discipline as every prior card this investigation (`CAMPY_MCP_CMD` pointing at the sibling `hippocampy` repo, `campy status` check first, full `tee`'d output to a log file read completely, generous timeout). A live smoke run showing `annatar_degraded` correctly `false` through any second-veto cycles is corroborating evidence if the scenario happens to occur (puzzle-dependent, double vetoes aren't guaranteed in any given run) — the TDD suite is the primary evidence either way, per the card's own explicit fallback standard.

## Assumptions/defaults

- One-line fix, exact placement mirrored from the two existing correct call sites — no design ambiguity here, just confirm current line numbers before editing.
