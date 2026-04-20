# A-026 - Realign `_enforce_action_policy` Tests With A023 Probe Rationale

## Card metadata

- Card: A026
- Priority: P2
- Layer: ARC runtime tests
- Depends on: A023

## Summary

Update two assertions in `tests/test_arc3_orchestrator.py` so they match the A023 rationale format. Both tests exercise `_enforce_action_policy` with `_consecutive_no_progress_steps=2` and untested actions available — conditions that now trigger the A023 proactive coverage probe rather than the B154 `exploration step N/5` branch. No production code change.

## Implementation approach

### 1. Confirm A023 is the only-reachable branch under the test fixture

In `agents/arc3/orchestrator.py:4659-4696`, the A023 untested-action probe runs inside `_enforce_action_policy` when all three conditions hold:

1. `unexplored = set(untested_actions) & set(available_actions)` is non-empty.
2. `self._consecutive_no_progress_steps >= 2`.
3. The chosen `action_id` is not already in `unexplored`.

Both tests set `_consecutive_no_progress_steps = 2`, provide untested actions, and pick an *explored* action first — so the probe fires and rewrites the returned dict before B154 gets a chance.

### 2. Update assertions

In `tests/test_arc3_orchestrator.py`:

- `test_policy_override_forces_unexplored_action` (line 866-890): keep `action_id == "ACTION3"`; replace `assert "exploration step 1/5 (level 1)" in action["rationale"]` with
  ```python
  assert action["decision_source"] == "policy_untested_probe"
  assert "A023 proactive coverage probe" in action["rationale"]
  ```
- `test_policy_override_broadens_exploration_after_decay` (line 893-917): same substitution.

### 3. Tests to run

- `pytest -q tests/test_arc3_orchestrator.py` — both updated tests pass; no other test regresses.
- `make test-a` — A-series regression suite.

## Concrete file additions/edits

- edit `tests/test_arc3_orchestrator.py`:
  - swap assertion strings as above.

## API/interface changes

None — test-only.

## Tests to add or run

- `pytest -q tests/test_arc3_orchestrator.py`
- `make test-a`

## Validation commands

```sh
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_arc3_orchestrator.py::test_policy_override_forces_unexplored_action \
  tests/test_arc3_orchestrator.py::test_policy_override_broadens_exploration_after_decay
```

Expected: `2 passed`.

## Assumptions/defaults

- A023's coverage-probe override is the intended behavior; these tests were pre-A023 assertions that incidentally fell through the probe gate. If a future card re-separates B154 exploration-count reporting from A023's rationale, this test may need to assert both channels.
