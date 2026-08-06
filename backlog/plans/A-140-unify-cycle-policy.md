# Plan: A140 — Unify Cycle Policy Between Inline and Temporal Workflows

## Context

Two orchestrators implement the same loop policy:

**Inline** — `agents/arc4/workflow.py`:
- Budget: line ~43 `if state.step_index >= self._limits.max_cycles`
- Stall guard: lines ~144-162 (multi-pass logic: keep exploring while untested actions remain; stall only when `no_progress >= num_available * 2`)
- Falsification recording: `_record_evaluation_state` lines ~184-193 (resets no_progress on progress, else increments and bumps `action_falsification_counts[action_id]` by `max(1, falsification_delta)`)
- Terminate: line ~164

**Temporal** — `agents/arc4/temporal_workflows.py` (`ArcPuzzleWorkflow.run`):
- Budget: lines ~36-38
- No-progress/falsification accounting: lines ~140-151 (dict-based mirror of `_record_evaluation_state`)
- Stall guard: lines ~154-167 (synced to inline on 2026-06-11; will drift again)
- Terminate: lines ~169-171

Constraint: Temporal workflow code runs in a sandbox that restricts imports and nondeterminism. A module of **pure functions over primitives/dicts with stdlib-only imports** is safe to import into a workflow file. Do not import anything heavy (no httpx, no adapter modules) into `cycle_policy.py`.

State shape difference: inline uses the `WorkflowState` dataclass; Temporal keeps `self._state` as a plain dict (`state.to_dict()` shape). Policy functions must work with both → take scalars in, or accept a mutable mapping for the recording function.

## Implementation Steps

### Step 1: Create `agents/arc4/cycle_policy.py`

```python
"""Pure cycle-policy functions shared by the inline and Temporal orchestrators.

Temporal-sandbox-safe: stdlib only, no I/O, deterministic.
"""

from __future__ import annotations

from typing import Any, MutableMapping


def check_budget(step_index: int, max_cycles: int) -> str | None:
    """Return "budget_exhausted" when the cycle budget is spent."""
    if step_index >= max_cycles:
        return "budget_exhausted"
    return None


def check_stall(
    consecutive_no_progress: int,
    max_consecutive_no_progress: int,
    num_available_actions: int,
    num_attempted_actions: int,
) -> str | None:
    """Return "stall_detected" only after every action has been tried at least
    twice with zero progress. Actions may behave differently as game state
    evolves, so one pass through the action space is not enough evidence.
    """
    if consecutive_no_progress < max_consecutive_no_progress:
        return None
    num_available = num_available_actions or 1
    if num_available - num_attempted_actions > 0:
        return None  # untested actions remain — keep exploring
    if consecutive_no_progress >= num_available * 2:
        return "stall_detected"
    return None


def record_evaluation_outcome(
    *,
    no_progress_count: int,
    falsification_counts: MutableMapping[str, int],
    action_key: str,
    meaningful_progress: bool,
    falsification_delta: int,
) -> int:
    """Update falsification counts in place; return the new no-progress count."""
    if meaningful_progress:
        return 0
    falsification_counts[action_key] = falsification_counts.get(action_key, 0) + max(1, falsification_delta)
    return no_progress_count + 1


def termination_from_evaluation(decision: str | None, reason: str | None) -> tuple[str, str] | None:
    """Map an evaluator decision to (status, reason) or None to continue."""
    if str(decision or "").lower() == "terminate":
        return ("terminated", reason or "terminated")
    return None
```

### Step 2: Rewire `workflow.py`

Replace line ~43 budget check with `if check_budget(state.step_index, self._limits.max_cycles): return self._finish(state, WorkflowStatus.BUDGET_EXHAUSTED, "budget_exhausted", phase_results)`.

Replace the stall block (lines ~144-162, including the diagnostic STALL_CHECK logging added 2026-06-11 — keep the log line, feed it the same values) with a call to `check_stall(state.consecutive_no_progress_count, self._limits.max_consecutive_no_progress, len(current_observation.get("available_actions", [])), len(state.action_attempt_counts))`; on `"stall_detected"` finish with `WorkflowStatus.STALLED`.

Replace the body of `_record_evaluation_state` with a call to `record_evaluation_outcome(...)`, assigning the return to `state.consecutive_no_progress_count`. Keep the method as a thin wrapper so call sites don't change.

### Step 3: Rewire `temporal_workflows.py`

Import the four functions at module top (sandbox-safe). Replace lines ~36-38, ~140-151, ~154-167, ~169-171 with the equivalent calls operating on `self._state` dict fields. The diff should net-delete ~30 lines.

### Step 4: Tests

**File:** `tests/test_a140_cycle_policy.py` (new)

1. `test_check_budget_boundary` — step 9/max 10 → None; 10/10 → exhausted
2. `test_check_stall_below_threshold` — no_progress 3, max 4 → None
3. `test_check_stall_untested_remaining` — 5 available, 3 attempted, no_progress 6 → None
4. `test_check_stall_two_full_passes` — 5 available, 5 attempted, no_progress 10 → "stall_detected"; no_progress 9 → None
5. `test_check_stall_empty_action_list` — num_available 0 treated as 1 (no div-by-zero, stalls at 2)
6. `test_record_outcome_progress_resets` — returns 0, counts untouched
7. `test_record_outcome_falsification_floor` — delta 0 still increments count by 1
8. `test_termination_mapping` — "terminate"/"TERMINATE" → tuple; "continue"/None → None

Also assert parity: run the existing workflow tests and temporal tests unmodified.

### Step 5: Verify

```bash
make test-a
.venv/bin/python -m pytest tests/test_a140_cycle_policy.py -q
.venv/bin/python -m pytest tests/ -q -k "workflow or temporal or stall" \
  --ignore=tests/test_arc3_meta_harness_query.py --ignore=tests/test_b168_graph_exploration.py \
  --ignore=tests/test_b171_action_fact_persistence.py --ignore=tests/test_b174_chunk_ledger_persistence.py \
  --ignore=tests/test_model_constraints.py
```

Grep-check the deduplication is real:

```bash
grep -n "num_available \* 2\|consecutive_no_progress" agents/arc4/workflow.py agents/arc4/temporal_workflows.py
# expect: only cycle_policy.py contains the arithmetic
```

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/cycle_policy.py` | New — 4 pure policy functions |
| `agents/arc4/workflow.py` | Delegate budget/stall/recording/terminate |
| `agents/arc4/temporal_workflows.py` | Same delegation; net-delete duplicated blocks |
| `tests/test_a140_cycle_policy.py` | New, 8 tests |

## Conflict Note (for fan-out)

Touches `workflow.py` and `temporal_workflows.py` — conflicts with A139 (book_id keying). If A139 lands first, fold its `book_id` key choice into `record_evaluation_outcome`'s `action_key` argument (the policy function already takes the key as a parameter, so the merge is mechanical).

## Risks

- Temporal sandbox import restrictions: keep `cycle_policy.py` stdlib-only; verify by running the temporal tests (they exercise workflow definition validation).
- Silent behavior drift during translation: mitigate by changing one block at a time and running the parity test suite between steps.
