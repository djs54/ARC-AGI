# Plan A-032 — Realign B176 and B185 with post-A017 / B215 production gates

## Card metadata

- **Card:** `backlog/A032.md`
- **Layer:** evaluation/harness (test-only)
- **Priority:** P2
- **Depends on:** A017, A029, A031

## Summary

Two B-series test files still failed after A031 because they encoded production assumptions that changed later:

1. **b185** — `test_replan_target_escalates_when_signature_repeats` and `test_replan_target_allows_route_when_signature_changes` both assert on `runner._replan_target(...)` as if it returns a bare `SolvePhase`. A017 changed the return type to `tuple[SolvePhase, str]`.
2. **b176** — `test_plateau_lock_threshold_decay` constructs a context with a single observed action family. The B215 `MIN_DISTINCT >= 3` gate at `agents/arc3/solver.py:3079` rejects plateau activation for this fixture, so `_plateau_locked_family` stays `None`.

Both are test-only drift. Production is correct.

## Implementation approach

### b185 (two tests)

Mirror the pattern used in A031:

```python
# before
first = runner._replan_target(orchestrator)
second = runner._replan_target(orchestrator)
assert first is SolvePhase.ROUTE
assert second is SolvePhase.MODEL

# after
first_phase, first_reason = runner._replan_target(orchestrator)
second_phase, second_reason = runner._replan_target(orchestrator)
assert first_phase is SolvePhase.ROUTE
assert first_reason == "rebuild_route_from_saturation"
assert second_phase is SolvePhase.MODEL
assert second_reason == "signature_escalation"
```

The second test (no escalation) checks both calls yield `(ROUTE, "rebuild_route_from_saturation")` after the signature changes.

### b176 (one test)

Add the minimal fixture needed to clear the B215 gate:

```python
ctx = {
    "observed_action_effects": [{"action": "ACTION1", "avg_meaningful_change": 2.0, "zero_reward_streak": 0}],
    "consecutive_zero_reward_steps": 10,
    "last_transition_effect": {"reward_signal": 0.0, "meaningful_change_score": 1.0},
    # A032: clear the B215 MIN_DISTINCT gate (solver.py:3076) that requires
    # >= 3 distinct tried families before plateau mode activates.
    "action_coverage": {"tested_count": 3},
}
```

The solver reads `tested_count` first (solver.py:3081), so inline bookkeeping via `action_coverage` is a cleaner knob than fabricating two extra `observed_action_effects` entries that would also perturb `_score_action_families`.

## Concrete file edits

- `tests/test_b185_failure_taxonomy.py` — two assertion blocks around lines 132–137 and 158–163 rewritten to unpack and assert `reason`.
- `tests/test_b176_plateau_explore_untried.py` — one context dict around lines 76–80 gains `action_coverage={"tested_count": 3}` plus a short comment pointing at solver.py:3076.

No production file changes.

## API / interface changes

None.

## Tests to add or run

```bash
.venv/bin/python -m pytest -v tests/test_b185_failure_taxonomy.py tests/test_b176_plateau_explore_untried.py
make test-a
```

Both must be green.

## Assumptions / defaults

- The B215 MIN_DISTINCT gate at `solver.py:3076-3095` is intended production behavior and should not be relaxed from the solver side.
- `tested_count` takes precedence over `len(observed_action_effects)` for the gate's distinct-count calculation (confirmed by reading `solver.py:3084`).
- A031's tuple-unpack pattern is the canonical fix for the `_replan_target` drift class.
