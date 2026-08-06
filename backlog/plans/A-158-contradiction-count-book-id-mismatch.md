# Plan: A158 — `world_model_contradiction_count` Structurally Never Reflects ACTION6 History

## Context

`agents/arc4/telemetry.py::_step_snapshot` (current line 164):

```python
"world_model_contradiction_count": int(getattr(state, "action_falsification_counts", {}).get(self._action_id() or "", 0)) if state is not None else 0,
```

`_action_id()` (current lines 230-236) returns `self._latest_execution.action_id` — always the base action id (`"ACTION6"`), never the coordinate book_id (`"ACTION6@x,y"`) that `state.action_falsification_counts` is actually keyed by for click actions (confirmed via `agents/arc4/plan_generator.py`'s per-target bookkeeping and A147's `base_action()`/`count_base_actions()` helpers, which exist precisely because book_id-vs-base-action mismatches like this are a known shape of bug in this codebase).

`execution.candidate.metadata["book_id"]` (set in `plan_generator.py::_build_candidates`, carried through `_to_plan_candidate`) already carries the correct key.

## Implementation Steps

### Step 1: Prefer `book_id` when available

In `agents/arc4/telemetry.py::_step_snapshot`, add a small helper (near `_action_id`, or inline) to resolve the lookup key:

```python
def _contradiction_lookup_key(self) -> str | None:
    execution = self._latest_execution
    if execution is not None and execution.candidate is not None:
        book_id = execution.candidate.metadata.get("book_id") if isinstance(execution.candidate.metadata, Mapping) else None
        if book_id:
            return str(book_id)
    return self._action_id()
```

Then change current line 164 from:

```python
"world_model_contradiction_count": int(getattr(state, "action_falsification_counts", {}).get(self._action_id() or "", 0)) if state is not None else 0,
```

to:

```python
"world_model_contradiction_count": int(getattr(state, "action_falsification_counts", {}).get(self._contradiction_lookup_key() or "", 0)) if state is not None else 0,
```

Place the new helper as a regular method near `_action_id` (current lines 230-236) so it has access to `self._latest_execution` the same way.

### Step 2: Tests

New file `tests/test_a158_contradiction_count_book_id.py`. Follow the `ArcV2Telemetry` direct-construction pattern from `tests/test_a156_step_failure_class_overload.py`/`tests/test_a157_reasoning_escalation_count.py` — construct `ArcV2Telemetry(...)`, set `_latest_execution` directly to a real `ExecutionResult` (with a `PlanCandidate` carrying `metadata={"book_id": ...}`), set a `WorkflowState` with `action_falsification_counts` populated, and call `_step_snapshot((state,))` (the state needs to be discoverable via `_extract_state`, which scans `args` for a `WorkflowState` instance — pass it positionally in the args tuple).

1. `test_action6_book_id_falsification_count_now_visible` — `execution.candidate.metadata = {"book_id": "ACTION6@22,0"}`, `state.action_falsification_counts = {"ACTION6@22,0": 3}` — assert `snapshot["world_model_contradiction_count"] == 3` (previously always 0 — this is the regression guard for the exact bug found).
2. `test_non_action6_bare_action_id_unaffected` — `execution.action_id = "ACTION1"`, `execution.candidate.metadata = {}` (no book_id), `state.action_falsification_counts = {"ACTION1": 2}` — assert `snapshot["world_model_contradiction_count"] == 2` (regression guard: base-action-id lookup still works when there's no book_id).
3. `test_missing_execution_defaults_to_zero_no_crash` — `_latest_execution = None` — assert `snapshot["world_model_contradiction_count"] == 0`, no exception.
4. `test_candidate_without_book_id_metadata_falls_back_to_action_id` — `execution.candidate.metadata = {"something_else": True}` (present but no `book_id` key), `execution.action_id = "ACTION2"`, `state.action_falsification_counts = {"ACTION2": 1}` — assert `snapshot["world_model_contradiction_count"] == 1`.

## Verify

```bash
.venv/bin/python -m pytest tests/test_a158_contradiction_count_book_id.py -v
make test-a
make test-all
```

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/telemetry.py` | New `_contradiction_lookup_key` helper preferring `execution.candidate.metadata["book_id"]`, falling back to `_action_id()`; `world_model_contradiction_count` uses it |
| `tests/test_a158_contradiction_count_book_id.py` | New, 4 tests |

## Risks

- Low — additive helper, existing fallback path (`_action_id()`) preserved exactly for the no-book_id case, so non-ACTION6 behavior is provably unchanged (test 2).
