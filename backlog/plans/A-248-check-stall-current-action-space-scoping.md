# A248 — `check_stall` Current-Action-Space Scoping: Plan

## Card metadata

- Card: `backlog/A248.md`
- Depends on: A230 (the fix precedent this mirrors), A242/A243 (same bug category, already fixed twice), A202 (the mechanism whose input this corrects)

## Design (confirmed by direct read before writing this plan)

- `agents/arc4/workflow.py:434-448` — computes `num_available`, `num_attempted`, `untested_remaining`, logs `STALL_CHECK`, then calls `check_stall`.
- `agents/arc4/cycle_policy.py:22-24` — `count_base_actions`/`base_action` (the `ACTION6@x,y` → `ACTION6` collapse).
- `agents/arc4/cycle_policy.py:47-59` — `check_stall`'s own internal `num_available - num_attempted_actions > 0` comparison.
- `agents/arc4/annatar_signals.py:251-249` (approximate — confirm exact line numbers before writing the real diff) — `if stall_reason is not None: all_falsified = True; untested_remaining = False`.
- `agents/arc4/types.py:515` — `WorkflowState.action_attempt_counts: dict[str, int]`, confirmed never reset anywhere in the codebase.

### The fix

1. In `workflow.py`, replace the raw subtraction with a set-based computation:

```python
from agents.arc4.cycle_policy import base_action  # or wherever base_action is importable from

available_actions = current_observation.get("available_actions", [])
num_available = len(available_actions)
num_attempted = count_base_actions(state.action_attempt_counts)
attempted_base_actions = {base_action(k) for k in state.action_attempt_counts}
untested_remaining = len(set(available_actions) - attempted_base_actions)
```

(Illustrative — confirm `available_actions`' actual element shape before writing the real diff: are entries already base-action strings like `"ACTION6"`, or something else? `PLANNER obs_available_actions=['ACTION1', 'ACTION2', 'ACTION3', 'ACTION4']` from the live log strongly suggests they're already bare base-action strings needing no further collapsing, but confirm directly against `current_observation`'s real shape/type before assuming.)

2. Change `check_stall`'s signature to accept the correctly-scoped count directly, rather than recomputing an internal subtraction from two differently-scoped raw counts:

```python
def check_stall(
    consecutive_no_progress: int,
    max_consecutive_no_progress: int,
    num_available_actions: int,
    num_untested_remaining: int,
) -> str | None:
    """Return stall_detected once all actions are repeatedly non-productive."""
    if num_available_actions > 0 and num_untested_remaining > 0:
        return None
    if consecutive_no_progress >= stall_threshold(max_consecutive_no_progress, num_available_actions):
        return "stall_detected"
    return None
```

(Illustrative signature/body — confirm this doesn't break any other caller of `check_stall` before finalizing; grep for all call sites first. `temporal_workflows.py:175` also calls `count_base_actions` in a parallel Temporal-workflow code path — check whether it independently computes/needs the same fix, since it appears to mirror `workflow.py`'s logic for the Temporal-sandbox-safe orchestrator variant. If it does, this card's scope includes fixing it there too for consistency, not just the inline orchestrator; if the two paths have already diverged for an unrelated reason, document why the Temporal path doesn't need the same fix rather than silently skipping it.)

3. Update the `STALL_CHECK` log line (`workflow.py:441-448`) to log the corrected `untested_remaining`, so the diagnostic and the real decision input report the same number — matching `stall_threshold`'s own stated reason for existing as a shared helper (`cycle_policy.py:34-44`'s docstring).

### Regression discipline

- When an episode's `available_actions` never changes composition (the common case — most puzzles offer a fixed action set for their whole run), `attempted_base_actions` will always be a subset relationship consistent with the old formula's behavior in the "no stale actions" case — confirm this with a specific regression test, not just by inspection.
- The fix must not change `stall_threshold`'s own formula or `check_stall`'s threshold-comparison branch — only the "genuinely untested actions remain" early-return's input.

## Implementation approach

### Files

- Modify: `agents/arc4/workflow.py` — the `STALL_CHECK` computation block (~line 434-448) and the `check_stall(...)` call (~line 449-454).
- Modify: `agents/arc4/cycle_policy.py` — `check_stall`'s signature/body.
- Check and, if needed, modify: `agents/arc4/temporal_workflows.py` (~line 145-175) — parallel Temporal-workflow code path; confirm whether it independently reimplements the same buggy comparison.
- Test: new `tests/test_a248_check_stall_current_action_space.py`.

### TDD

- New test: `available_actions` composition never changes across the episode (e.g., always `['ACTION1', 'ACTION2']`) — `check_stall`'s behavior (both the early-return and the threshold-fallback) is byte-identical to the pre-fix formula's result for every combination of attempted/no-progress values exercised. This is the critical regression guard — write it first, confirm it captures today's exact behavior before changing anything.
- New test: `action_attempt_counts` contains a base action (e.g., `ACTION6@10,20`) not present in the current cycle's `available_actions` (simulating the exact live scenario: an earlier probe-phase click no longer available in a later goal-directed phase) — confirm the corrected `untested_remaining`/`check_stall` input is non-negative and reflects only the current phase's genuinely-untested actions, not the stale entry.
- New test: same stale-action scenario, but with `consecutive_no_progress` at/above `stall_threshold` — confirm `check_stall` returns `"stall_detected"` if-and-only-if there are truly zero genuinely-untested actions in the *current* available set, not merely because a stale historical action inflated the old raw count.
- New test (in `annatar_signals.py`'s own test file, or a new one covering this specific interaction): `stall_reason="stall_detected"`'s override of `all_falsified`/`untested_remaining` only fires when the corrected `check_stall` computation genuinely supports it — reproduce this card's own live scenario as a deterministic unit test (a fake `available_actions`/`action_attempt_counts` pair matching the log's exact composition) and confirm the pre-fix code would have (or did) let `untested` go negative in a way that changes `check_stall`'s output, then confirm the post-fix code does not.
- Regression: every existing `tests/test_a140_cycle_policy.py`, `tests/test_a202_*.py`, and any other `check_stall`/`STALL_CHECK`-adjacent test continues to pass unchanged (find them via `grep -rl "check_stall\|STALL_CHECK" tests/`).

### Validation commands

```bash
.venv/bin/python -m pytest tests/test_a248_check_stall_current_action_space.py -v
.venv/bin/python -m pytest tests/test_a140_cycle_policy.py -v
make test-a
make test-all
make check-compliance
```

### Live-verify

Same environment/discipline as every prior card this investigation (`CAMPY_MCP_CMD` absolute path pointing at the sibling `hippocampy` repo's `.venv`, `campy status` check before starting, full `tee`'d output to a log file and read the complete file rather than a truncated tail, generous timeout — for a run of 60+ steps, run it with `run_in_background: true` and a `timeout` of at least 600000ms, since a 100-step run in this same investigation took roughly 5 minutes). Ideally reproduce a probe-to-goal-directed phase transition (common in this codebase's normal runs, per this card's own evidence) and confirm `STALL_CHECK`'s `untested` field is never negative across the whole run. Puzzle assignment is random — report honestly what was actually observed, including if no phase-composition-change scenario happens to occur in whichever puzzle is assigned (in that case, the TDD/unit-test coverage above is the primary evidence for this card, same authorized substitute standard as A237/A244).

## Assumptions/defaults

- `available_actions`' elements are assumed to already be bare base-action strings (not book_ids needing `base_action()` collapsing) based on the live log evidence (`obs_available_actions=['ACTION1', 'ACTION2', 'ACTION3', 'ACTION4']`) — confirm directly against the real runtime type before assuming, since it affects whether the fix needs `base_action()` applied to `available_actions` too or only to `state.action_attempt_counts`'s keys.
- If `temporal_workflows.py` turns out to have an independent, already-divergent reason for not sharing this bug (e.g., it doesn't track cross-phase action-space changes the same way), document that finding precisely rather than fixing something that isn't actually broken there.
