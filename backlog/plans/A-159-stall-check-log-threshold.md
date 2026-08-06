# Plan: A159 — `STALL_CHECK` Diagnostic Log Reports the Wrong Effective Threshold

## Context

`agents/arc4/cycle_policy.py::check_stall` gates on two independent conditions:

```python
def check_stall(consecutive_no_progress, max_consecutive_no_progress, num_available_actions, num_attempted_actions):
    if consecutive_no_progress < max_consecutive_no_progress:
        return None
    num_available = num_available_actions or 1
    if num_available_actions > 0 and num_available - num_attempted_actions > 0:
        return None
    if consecutive_no_progress >= num_available * 2:
        return "stall_detected"
    return None
```

`agents/arc4/workflow.py`'s `STALL_CHECK` diagnostic log line (current lines 152-159) hardcodes its displayed `threshold` to `(num_available or 1) * 2` — only half of what `check_stall` actually requires. The real effective threshold is `max(max_consecutive_no_progress, num_available * 2)`, since both conditions must hold. Live evidence: game `r11l-495a7899` (single-action game, `num_available=1`) — log showed `threshold=2` at every step, but the run only actually stalled once `no_progress` reached 4 (`max_consecutive_no_progress`), not 2.

Rather than just patching the log line's inline arithmetic to duplicate `max(...)` (which would still risk drifting from `check_stall`'s real logic if either changes independently later), extract the threshold computation into a single shared pure function in `cycle_policy.py` — the module whose own docstring states its purpose is exactly this: "Pure cycle-policy functions shared by inline and Temporal orchestrators." Both `check_stall` and the log line then read from the same source, so they cannot drift apart again.

## Implementation Steps

### Step 1: Add `stall_threshold` to `cycle_policy.py`

Add a new function, placed above `check_stall`:

```python
def stall_threshold(max_consecutive_no_progress: int, num_available_actions: int) -> int:
    """Effective consecutive-no-progress threshold check_stall gates on.

    Both max_consecutive_no_progress (a fixed floor) and num_available*2 (a
    coverage-scaled ceiling) must be satisfied for a stall to fire — the
    real threshold is whichever is larger. Exists as its own function so
    diagnostic logging (workflow.py's STALL_CHECK line) can report the same
    number check_stall actually gates on, instead of duplicating half the
    formula and drifting out of sync with it.
    """
    return max(max_consecutive_no_progress, (num_available_actions or 1) * 2)
```

### Step 2: Rewrite `check_stall` to use it (behavior-preserving)

```python
def check_stall(
    consecutive_no_progress: int,
    max_consecutive_no_progress: int,
    num_available_actions: int,
    num_attempted_actions: int,
) -> str | None:
    """Return stall_detected once all actions are repeatedly non-productive."""
    num_available = num_available_actions or 1
    if num_available_actions > 0 and num_available - num_attempted_actions > 0:
        return None
    if consecutive_no_progress >= stall_threshold(max_consecutive_no_progress, num_available_actions):
        return "stall_detected"
    return None
```

This is mathematically identical to the original (`A and B` ⟺ `consecutive_no_progress >= max_consecutive_no_progress AND consecutive_no_progress >= num_available*2` ⟺ `consecutive_no_progress >= max(max_consecutive_no_progress, num_available*2)`) — confirm via the existing `tests/test_a140_cycle_policy.py` and `tests/test_a147_stall_base_action.py` suites (both already exercise `check_stall`) passing unchanged after this refactor, which is the regression proof that behavior didn't change.

Add `"stall_threshold"` to the module's `__all__` list (current lines 73-80).

### Step 3: Fix the log line in `workflow.py`

Import `stall_threshold` alongside the existing `cycle_policy` imports (current line 9: `from .cycle_policy import check_budget, check_stall, count_base_actions, record_evaluation_outcome, termination_from_evaluation` — add `stall_threshold` to this list).

Change the log call (current lines 152-159) from:

```python
_logging.getLogger(__name__).info(
    "STALL_CHECK no_progress=%d, available=%d, attempted=%d, untested=%d, threshold=%d",
    state.consecutive_no_progress_count,
    num_available or 1,
    num_attempted,
    untested_remaining,
    (num_available or 1) * 2,
)
```

to:

```python
_logging.getLogger(__name__).info(
    "STALL_CHECK no_progress=%d, available=%d, attempted=%d, untested=%d, threshold=%d",
    state.consecutive_no_progress_count,
    num_available or 1,
    num_attempted,
    untested_remaining,
    stall_threshold(self._limits.max_consecutive_no_progress, num_available),
)
```

### Step 4: Tests

New file `tests/test_a159_stall_check_log_threshold.py` (pure-function tests, no orchestrator/log-capture needed since the fix moved the logic into a testable `cycle_policy.py` function):

1. `test_threshold_uses_floor_when_larger` — `stall_threshold(max_consecutive_no_progress=4, num_available_actions=1)` returns `4` (not `2`) — this is the exact regression guard for the bug found (single-action game, `4 > 1*2`).
2. `test_threshold_uses_coverage_ceiling_when_larger` — `stall_threshold(max_consecutive_no_progress=4, num_available_actions=6)` returns `12` (`6*2=12 > 4`) — confirms the un-broken case (large action space) is unaffected.
3. `test_threshold_handles_zero_available_actions` — `stall_threshold(max_consecutive_no_progress=4, num_available_actions=0)` returns `4` (matches the `or 1` fallback: `max(4, 1*2)=4`).
4. `test_check_stall_still_matches_original_behavior_at_floor` — `check_stall(consecutive_no_progress=2, max_consecutive_no_progress=4, num_available_actions=1, num_attempted_actions=1)` returns `None` (2 < effective threshold 4) — regression guard proving the refactor didn't change `check_stall`'s actual gating, matching the exact scenario from the live evidence where the run correctly did NOT stop at `no_progress=2`.
5. `test_check_stall_fires_at_floor` — same setup with `consecutive_no_progress=4` — returns `"stall_detected"`.

## Verify

```bash
.venv/bin/python -m pytest tests/test_a159_stall_check_log_threshold.py -v
.venv/bin/python -m pytest tests/test_a140_cycle_policy.py tests/test_a147_stall_base_action.py -v
make test-a
make test-all
```

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/cycle_policy.py` | New `stall_threshold` function; `check_stall` rewritten to use it (behavior-preserving); `__all__` updated |
| `agents/arc4/workflow.py` | `STALL_CHECK` log line uses `stall_threshold(...)` instead of the bare `(num_available or 1) * 2` |
| `tests/test_a159_stall_check_log_threshold.py` | New, 5 tests |

## Risks

- Low — the `check_stall` rewrite is a pure algebraic simplification (proven equivalent above), and the existing `test_a140_cycle_policy.py`/`test_a147_stall_base_action.py` suites already provide behavior-regression coverage for `check_stall` specifically.
