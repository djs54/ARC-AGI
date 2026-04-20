# A-027 - Realign B137 Plan-Dedup Tests With A024 Fingerprint Semantics

## Card metadata

- Card: A027
- Priority: P2
- Layer: ARC runtime tests
- Depends on: A024

## Summary

Update `tests/test_b137_plan_dedup.py` so dedup-path tests populate the A024 fingerprint caches (`_last_registered_{top,chunk}_fingerprint`) instead of the legacy dicts, and so the skip-reason assertion matches the A024 string `"identical_fingerprint"`. No production code change.

## Implementation approach

### 1. Confirm the A024 semantics

In `agents/arc3/solver.py:3376-3403`, `_plan_changed` reads only the fingerprint cache — the legacy `_last_registered_chunk_plan` dict is no longer consulted. Fingerprint shape (from `_plan_fingerprint`, L3406-3430):

```
(plan_type, goal, tuple(steps or []), archetype_str, vc_type_str, chunk_desc_if_chunk_else_None)
```

For a bare `SolveEngine(brain_client=..., llm_client=..., session_id=...)` instance in the fixture, `_archetype` is `None` and `_victory_condition` is `None`, so both `archetype_str` and `vc_type_str` default to `"unknown"`. The simplest cache-population strategy in tests is to call `_plan_fingerprint(...)` with the same args the test will later pass to `_plan_changed(...)` — that guarantees parity without duplicating the tuple shape.

### 2. Update the three tests

In `tests/test_b137_plan_dedup.py`:

- **`test_plan_changed_returns_false_for_identical_plan`** (L42-55): replace
  ```python
  solver._last_registered_chunk_plan = {"goal": ..., "steps": [...]}
  ```
  with
  ```python
  solver._last_registered_chunk_fingerprint = solver._plan_fingerprint(
      plan_type="chunk", goal="Test goal", steps=["step1", "step2"],
  )
  ```

- **`test_plan_changed_force_flag_always_returns_true`** (L83-95): same cache swap. Not strictly required (the `force=True` path short-circuits before the cache read), but keeping the setup consistent prevents confusion and catches any future regression that moves the force check.

- **`test_plan_changed_distinguishes_plan_types`** (L97-120): populate both fingerprint caches:
  ```python
  solver._last_registered_top_fingerprint = solver._plan_fingerprint(
      plan_type="top", goal="Top goal", steps=["step1"],
  )
  solver._last_registered_chunk_fingerprint = solver._plan_fingerprint(
      plan_type="chunk", goal="Chunk goal", steps=["step2"],
  )
  ```

### 3. Update the skip-reason assertion

At L168, replace
```python
assert trace_call[1]["metadata"]["reason"] == "identical_to_last_registered"
```
with
```python
assert trace_call[1]["metadata"]["reason"] == "identical_fingerprint"
```

### 4. Tests that intentionally do NOT need changes

- `test_plan_changed_returns_true_when_no_prior_plan` — fingerprint cache is `None` by default, `_plan_changed` returns `True`. No setup needed.
- `test_plan_changed_returns_true_for_different_goal` / `_for_different_steps` — these set the legacy dict but the fingerprint cache stays `None`, so `_plan_changed` still returns `True` for the "different" input. Assertions pass regardless.
- `test_register_chunk_plan_first_registration`, `..._changed_triggers_reregistration`, and the solve-plan counterparts — they exercise real `register_plan` flows through the mock brain, not direct `_plan_changed` calls with pre-populated cache.

## Concrete file additions/edits

- edit `tests/test_b137_plan_dedup.py`:
  - three cache-population swaps
  - one reason-string assertion swap

## API/interface changes

None — test-only.

## Tests to add or run

- `pytest -q tests/test_b137_plan_dedup.py` — expect 14 passed (up from 11 passed + 3 failed).
- `make test-a` — regression check.

## Validation commands

```sh
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_b137_plan_dedup.py
```

Expected: `14 passed`.

## Assumptions/defaults

- Tests accept `_plan_fingerprint` as a stable internal helper. If a future refactor renames it, this test suite updates alongside the rename.
- The legacy `_last_registered_{top,chunk}_plan` dicts are kept by A024 for audit-trail continuity; no test needs to exercise them.
