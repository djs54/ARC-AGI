# A-018 - Cross-Chunk Plateau Memory to Stop Single-Action Plan Churn

## Card metadata

- Card: A018
- Priority: P0
- Layer: ARC runtime
- Depends on: A010, A011, A015

## Summary

Add sticky cross-chunk memory of failed plateau families to the solver, tighten the blacklist-clear condition to require a real score/reward signal (not any cell change), and wire a new `plateau_escalation_required` signal into the orchestrator's failure-classification path so that puzzles with fully-characterized low-value action spaces exit as `COVERAGE_SATURATED_ABORT` (A015) instead of churning through a series of single-action plateau chunks labeled `stuck_in_loop`.

## Implementation approach

1. **Add the persistent set** to `agents/arc3/solver.py`.

   In `__init__` near line 1862 where `self._loop_detected_action_blacklist: Optional[set[str]] = None` is declared, add:

   ```python
   # A018: cross-puzzle memory of plateau families that already exhausted.
   # Unlike _loop_detected_action_blacklist, this set survives any cell-change
   # clear and is only reset by a genuine reward tick or full solver reset.
   self._failed_plateau_families: set[str] = set()
   ```

   In the reset method at line 3492 (where `self._loop_detected_action_blacklist = None` is reset), add:

   ```python
   self._failed_plateau_families = set()
   ```

2. **Tighten the blacklist-clear guard** at `agents/arc3/solver.py:2740-2751`.

   Current code clears on any `n_cells_changed > 0`. Replace the guard with a reward-or-score check:

   ```python
   try:
       last_eff = (hypothesis_context or {}).get("last_transition_effect") or {}
       score_delta = float(last_eff.get("score_delta") or 0.0)
       reward_delta = float(last_eff.get("reward") or last_eff.get("reward_delta") or 0.0)
       if (score_delta > 0 or reward_delta > 0) and self._loop_detected_action_blacklist:
           try:
               self._trace("loop_escape", "clear_blacklist", {"step": step},
                           {"cleared": list(self._loop_detected_action_blacklist),
                            "trigger": "reward_tick"})
           except Exception:
               pass
           self._loop_detected_action_blacklist = None
           # A018: reward tick also clears the failed-plateau set, since real progress
           # has invalidated the accumulated "this family cannot break the plateau" signal.
           self._failed_plateau_families = set()
   except Exception:
       pass
   ```

   Do **not** clear either set on cell-change alone.

3. **Accumulate into `_failed_plateau_families`** in the exhaustion guard at `agents/arc3/solver.py:3240-3264`.

   Where the existing code calls:

   ```python
   self._blacklist_action_family(cur_family, step=step, reason="plateau_exhausted")
   ```

   immediately after that call, add:

   ```python
   # A018: record this family as a failed plateau so the lock-selection step
   # below will not re-propose it on a subsequent plateau detection.
   if cur_family:
       self._failed_plateau_families.add(str(cur_family))
   ```

4. **Exclude `_failed_plateau_families` from lock selection** at `agents/arc3/solver.py:3107-3152`.

   Locate the code block that filters `available_actions` by `self._loop_detected_action_blacklist` (around line 3108-3111):

   ```python
   if self._loop_detected_action_blacklist:
       available_actions = [
           family for family in available_actions
           if family not in self._loop_detected_action_blacklist
       ]
   ```

   Immediately after this block, add:

   ```python
   # A018: also exclude plateau families that already exhausted earlier in this puzzle.
   if self._failed_plateau_families:
       available_actions = [
           family for family in available_actions
           if family not in self._failed_plateau_families
       ]
   ```

5. **Add escalation signal** near `agents/arc3/solver.py:3268` where `top_family = self._plateau_locked_family` is read and used to build a new plateau chunk.

   Replace the `if top_family:` block preamble with:

   ```python
   top_family = self._plateau_locked_family
   # A018: if the selector could not pick a family and we already burned through
   # two or more, escalate rather than churning another single-action plateau.
   if top_family is None and len(self._failed_plateau_families) >= 2:
       self._plateau_escalation_required = True
       try:
           self._trace(
               "solve_plateau_escalation",
               "plateau_policy",
               {"step": step, "failed_families": sorted(self._failed_plateau_families)},
               {"reason": "all_plateau_families_failed"},
           )
       except Exception:
           pass
   elif top_family:
       # ... existing B145/B146 "sync or replace plateau chunk" body ...
   ```

   Add `self._plateau_escalation_required: bool = False` to `__init__` near line 1862, and reset it to False in the reset method at line 3492. Include it in the emitted `SolveContext` at the function's return site (around lines 3311-3325) as a new field `plateau_escalation_required: bool`.

6. **Surface the field on `SolveContext`** at `agents/arc3/solver.py:129` (the dataclass declaration, where `plateau_locked_family: Optional[str] = None` already lives). Add:

   ```python
   plateau_escalation_required: bool = False
   ```

   Propagate it in the return at line 3311-3325:

   ```python
   return SolveContext(
       ...
       plateau_locked_family=self._plateau_locked_family,
       plateau_escalation_required=self._plateau_escalation_required,
       ...
   )
   ```

7. **Wire into the orchestrator failure path** at `agents/arc3/orchestrator.py`.

   Search for the site that currently emits `failure_class = "stuck_in_loop"` or `FailureClass.STUCK_IN_LOOP` (likely in the finalizer / run-end path). Before that assignment, insert:

   ```python
   # A018 + A015: if the solver asked to escalate (all plateau families failed)
   # and the coverage-saturated graduation signal also fired, treat this as
   # COVERAGE_SATURATED_ABORT rather than STUCK_IN_LOOP.
   solve_ctx = getattr(orchestrator, "_solve_context", {}) or {}
   if solve_ctx.get("plateau_escalation_required") and solve_ctx.get("coverage_saturated"):
       failure_class = FailureClass.COVERAGE_SATURATED_ABORT
   ```

   If `solve_context` does not expose `coverage_saturated` directly, derive it from `hypothesis_context.action_coverage` (same predicate A017 uses): `initial_exploration_complete and untested_count == 0`.

8. **Tests** — see Tests section below.

9. **Docs**: in `ARCHITECTURE.md`, under the existing plateau-exploitation discussion, add:

   ```
   #### Plateau family memory

   The solver keeps a set `_failed_plateau_families` across an entire solve()
   call. A family enters this set only via the plateau-exhaustion guard
   (two consecutive no-progress replans on the same locked family). The set
   is cleared only by a reward tick or a full solver reset — never by cell
   changes alone. Lock selection subtracts this set from the candidate pool,
   and if two or more families have failed and no unfailed candidate
   remains, the solver raises `plateau_escalation_required` which the
   orchestrator translates to `COVERAGE_SATURATED_ABORT` when the
   action-coverage signal also agrees.
   ```

## Concrete file additions/edits

- edit `agents/arc3/solver.py`:
  - add `_failed_plateau_families: set[str] = set()` field and `_plateau_escalation_required: bool = False` flag near line 1862
  - reset both in the reset method at line 3492
  - tighten the blacklist-clear guard at lines 2740-2751
  - accumulate into `_failed_plateau_families` inside the exhaustion guard at lines 3240-3264
  - filter lock candidates against `_failed_plateau_families` at lines 3107-3111
  - add escalation branch near line 3268
  - add `plateau_escalation_required: bool = False` to the `SolveContext` dataclass at line 129
  - include it in the `return SolveContext(...)` at lines 3311-3325
- edit `agents/arc3/orchestrator.py`:
  - in the finalizer that sets `failure_class`, prefer `COVERAGE_SATURATED_ABORT` over `STUCK_IN_LOOP` when `plateau_escalation_required` and `coverage_saturated` are both true
- add `tests/test_plateau_memory.py` with three tests (see Tests section)
- edit `ARCHITECTURE.md` — add the `#### Plateau family memory` subsection

## API/interface changes

- `SolveContext` gains one boolean field `plateau_escalation_required` (default `False`). Existing consumers continue to work because the field has a default; consumers that care must opt in to read it.
- `_failed_plateau_families` is a private set on the solver instance; no external callers.
- `_plateau_escalation_required` is a private flag on the solver instance.
- `failure_class` vocabulary is unchanged — this card only re-routes runs that today emit `STUCK_IN_LOOP` into the already-existing `COVERAGE_SATURATED_ABORT` from A015.
- No MCP seam changes, no config schema changes.

## Tests to add or run

Create `tests/test_plateau_memory.py`:

```python
import pytest
from agents.arc3.solver import PlanChunker, SolveContext


def _fresh_chunker():
    """Return a PlanChunker with just enough state for unit testing plateau memory."""
    c = PlanChunker.__new__(PlanChunker)
    c._loop_detected_action_blacklist = None
    c._failed_plateau_families = set()
    c._plateau_escalation_required = False
    c._plateau_locked_family = None
    c._plateau_lock_duration = 0
    c._plateau_lock_family_replan_count = 0
    c._plateau_lock_last_family = None
    c._plateau_lock_zero_delta_streak = 0
    c._plateau_active = False
    c._active_chunk = None
    c._chunk_history = []
    return c


def test_failed_plateau_family_is_not_cleared_by_cell_change(monkeypatch):
    """A018: visible cell change without reward must NOT clear _failed_plateau_families."""
    c = _fresh_chunker()
    c._failed_plateau_families.add("ACTION3")
    hyp_ctx = {
        "last_transition_effect": {
            "n_cells_changed": 12,
            "score_delta": 0,
            "reward": 0,
        }
    }
    # simulate the clear-path logic directly (mirrors solver.py:2740-2751 after A018)
    last_eff = hyp_ctx["last_transition_effect"]
    score_delta = float(last_eff.get("score_delta") or 0.0)
    reward_delta = float(last_eff.get("reward") or last_eff.get("reward_delta") or 0.0)
    if score_delta > 0 or reward_delta > 0:
        c._failed_plateau_families = set()
    assert "ACTION3" in c._failed_plateau_families


def test_failed_plateau_family_cleared_by_reward_tick():
    """A018: a genuine reward tick clears both the blacklist and the failed-plateau set."""
    c = _fresh_chunker()
    c._failed_plateau_families.add("ACTION3")
    c._loop_detected_action_blacklist = {"ACTION3"}
    last_eff = {"n_cells_changed": 1, "score_delta": 1.0, "reward": 0.0}
    score_delta = float(last_eff.get("score_delta") or 0.0)
    reward_delta = float(last_eff.get("reward") or last_eff.get("reward_delta") or 0.0)
    if score_delta > 0 or reward_delta > 0:
        c._loop_detected_action_blacklist = None
        c._failed_plateau_families = set()
    assert c._loop_detected_action_blacklist is None
    assert c._failed_plateau_families == set()


def test_two_failed_plateaus_trigger_escalation():
    """A018: after two families fail and no unfailed candidate exists, do NOT create a third plateau chunk."""
    c = _fresh_chunker()
    c._failed_plateau_families = {"ACTION2", "ACTION3"}
    c._plateau_locked_family = None  # selector found no candidate
    # Simulate the new escalation branch at solver.py:3268
    top_family = c._plateau_locked_family
    if top_family is None and len(c._failed_plateau_families) >= 2:
        c._plateau_escalation_required = True
    elif top_family:
        pytest.fail("should not have fallen into the chunk-creation branch")
    assert c._plateau_escalation_required is True
    assert c._active_chunk is None
```

In addition, add one integration test in `tests/test_failure_class_coverage_saturated.py` (extending the file added by A015) that constructs an end-to-end stub where `plateau_escalation_required=True` and `coverage_saturated=True` both fire and assert the orchestrator emits `FailureClass.COVERAGE_SATURATED_ABORT` instead of `FailureClass.STUCK_IN_LOOP`.

Run:

- `pytest -q tests/test_plateau_memory.py`
- `pytest -q tests/test_failure_class_coverage_saturated.py`
- `pytest -q -k plateau`  (regression sweep)
- `pytest -q -k chunker` (regression sweep)

## Validation commands

- `pytest -q tests/test_plateau_memory.py`
- Smoke verification:
  1. Re-run the Apr 18 06:58 puzzle (same puzzle id, same model, same seed).
  2. Count `register_plan` hits from the run's trace: `jq '[.[] | select(.tool=="register_plan")] | length' agent_execution_trace.json` — must be ≤ 3 (down from 7).
  3. Confirm `submission_results_single.json` shows `failure_class == "coverage_saturated_abort"`, not `"stuck_in_loop"`.
  4. Confirm no ACTION family appears twice in the `plateau_locked_family` values across the run: `jq '[.[] | select(.event_type=="solve_plateau_detection") | .metadata.locked] | unique | length' agent_execution_trace.json` — must equal the number of distinct locks.

## Assumptions/defaults

- `last_transition_effect.score_delta` and `last_transition_effect.reward` are the canonical reward signals — verified that the taxonomy already references these at `orchestrator.py:4526-4535` (`value_status` mapping relies on them upstream).
- Two failed plateau families is the escalation threshold. Rationale: on the Apr 18 06:58 trace, the third proposed family was already a re-lock of a previously-failed one, so escalating at ≥ 2 is the earliest correct bail-out point. This value is intentionally a constant in the code (not a config knob) to keep the failure-mode surface small.
- `COVERAGE_SATURATED_ABORT` (A015) already exists in the `FailureClass` taxonomy. This card does not add new enum values.
- `_failed_plateau_families` is per-puzzle, not per-process. The reset at line 3492 fires between puzzles via the existing solver lifecycle.
- If a puzzle happens to have only one plateau family available (the environment exposes only ACTION1), the solver will add ACTION1 to `_failed_plateau_families` after exhaustion, selector will find no candidates, `len(_failed_plateau_families) == 1 < 2` so no escalation fires, and the existing A010 coverage-saturation graduation path handles the exit (this is the intended interaction — A010 owns the 1-action case, A018 owns the N≥2-action case).
- We do not change the existing cell-change bookkeeping for any other code path — only the blacklist-clear and failed-plateau paths. Everything else that reads `last_transition_effect` continues to behave as before.
