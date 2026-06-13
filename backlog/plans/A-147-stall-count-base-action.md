# Plan: A147 — Fix Stall-Count Distortion From ACTION6 Book-ID Keying

## Context

- `agents/arc4/workflow.py` `_record_execution_attempt` keys `state.action_attempt_counts` by `book_id` (`(metadata).get("book_id") or action_id`), so ACTION6 click targets land as `ACTION6@x,y`.
- The stall guard computed `num_attempted = len(state.action_attempt_counts)` (inline) and `len(self._state["action_attempt_counts"])` (Temporal), counting each target separately.
- A140 keeps stall arithmetic in `agents/arc4/cycle_policy.py`; the count helper belongs there too so both orchestrators share one implementation.

## Implementation (completed)

### Step 1: helpers in `cycle_policy.py`

```python
def base_action(action_key: str) -> str:
    return str(action_key).split("@", 1)[0]

def count_base_actions(attempt_keys: Iterable[str]) -> int:
    return len({base_action(key) for key in attempt_keys})
```

Added to `__all__`. Stdlib-only, Temporal-sandbox-safe.

### Step 2: inline call site (`workflow.py`)

`num_attempted = count_base_actions(state.action_attempt_counts)` and import `count_base_actions`.

### Step 3: Temporal call site (`temporal_workflows.py`)

`num_attempted = count_base_actions(self._state.get("action_attempt_counts", {}))`; import added inside the `workflow.unsafe.imports_passed_through()` block.

### Step 4: tests — `tests/test_a147_stall_base_action.py`

1. `test_base_action_strips_coordinates` — `base_action("ACTION6@10,20") == "ACTION6"`; plain `ACTION1` unchanged
2. `test_count_base_actions_collapses_targets` — `{"ACTION1","ACTION6@1,1","ACTION6@2,2"}` → 2
3. `test_count_base_actions_empty` → 0
4. `test_check_stall_not_inflated_by_targets` — available=5, attempt keys = 4 base + 3 ACTION6 targets (= 5 base actions), no_progress below `5*2` → None (was prematurely affected before fix)
5. `test_check_stall_still_fires_after_two_passes` — all base actions tried, no_progress=10 → "stall_detected"

## Verify

```bash
make test-a
.venv/bin/python -m pytest tests/test_a147_stall_base_action.py tests/test_a139_action6_targeting.py tests/test_a140_cycle_policy.py -q
make test-all
```

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/cycle_policy.py` | `base_action`, `count_base_actions` |
| `agents/arc4/workflow.py` | stall uses `count_base_actions` |
| `agents/arc4/temporal_workflows.py` | stall uses `count_base_actions` |
| `tests/test_a147_stall_base_action.py` | New, 5 tests |

## Risks

- None material: collapsing keys only reduces `num_attempted`, which can only make the guard more conservative (less likely to stall prematurely). The multi-pass `*2` termination bound is unchanged.
