# Plan: A204 — Resume/Crash-Safety

## Card metadata

- ID: A204
- Priority: P0
- Layer: ARC runtime
- Dependencies: A200, A201, A202

## Summary

The highest-stakes card in this family. Implement write-ahead cycle recording bracketing the `execute` phase call (inside `WorkflowOrchestrator.run()`, not `executor.py`), and a startup resume check that reconciles any ambiguous in-flight action against the real ARC API observation before ever considering a retry. Read `docs/superpowers/specs/2026-08-23-trajectory-reasoner-design.md` §7 in full before starting — this plan implements that section precisely.

## Technical approach

### 1. Locate the real process entry point first

Before writing any code, trace the actual call chain from `run_single_puzzle.py` through to where `WorkflowOrchestrator` (or its Temporal-deprecated counterpart, which this card does not touch) is constructed and `run()` is first called, for a single live task. Read `run_single_puzzle.py`, `arc_runtime/runner_shell.py::SingleTaskRunner.initialize()` and whatever method actually dispatches a task, and `arc_runtime/bundle.py::build_arc_v2_bundle` in full. Identify:
- Where a fresh `WorkflowState` is currently constructed per task.
- What ARC API client object already exists and is reused for making live calls (do not construct a second one for this card's observation-reconciliation check).
- Where a `task_id` is available and stable for the duration of one puzzle attempt (needed for `start_or_resume_thread`).

Document these three findings at the top of this card's Resolution section once found, since they're load-bearing facts for everything else in this card.

### 2. `agents/arc4/workflow.py` — write-ahead bracketing

Read the current `WorkflowOrchestrator.run()` in full (post-A202, confirm exact current structure — this plan assumes A202 already added a `graph_port` reference to the orchestrator; confirm that assumption holds before proceeding). Locate the `execute` phase call:

```python
execution = self._invoke_phase(
    "execute",
    self._dependencies.execute,
    state,
    perception_payload,
    resolved_goal_payload,
    vet_payload,
)
```

Wrap it:

```python
cycle_id = None
anchor = state.active_investigation_anchor  # set by A202's run_reasoner_cycle; None if no thread is currently active
thread_id = anchor.get("thread_id") if anchor is not None else None
if self._graph_port is not None and thread_id is not None:
    write_cycle = getattr(self._graph_port, "write_cycle", None)
    if write_cycle is not None:
        try:
            write_result = write_cycle(thread_id, state.step_index, action_sent=True)
            cycle_id = write_result.get("cycle_id")
        except Exception:
            cycle_id = None

execution = self._invoke_phase(
    "execute",
    self._dependencies.execute,
    state,
    perception_payload,
    resolved_goal_payload,
    vet_payload,
)
phase_results.append(execution)
execution_payload = self._require_payload(execution, WorkflowPhase.EXECUTE)

if cycle_id is not None:
    confirm_cycle = getattr(self._graph_port, "confirm_cycle", None)
    if confirm_cycle is not None:
        try:
            confirm_cycle(cycle_id, decision="pending", confirmed=True)
        except Exception:
            pass
```

The thread_id lives in `state.active_investigation_anchor["thread_id"]`, the field A202's `run_reasoner_cycle` already establishes and maintains across cycles — this card does not need a new `WorkflowState` field for it. Confirm this field exists with this exact shape by reading A202's landed code first (its plan's `run_reasoner_cycle` sketch is the source of truth for the dict's keys: `anchor_ref`, `anchor_type`, `thread_id`, `state`, `deepening_cycle_count`, `already_retried`).

**Non-negotiable invariant:** `write_cycle` failing (any exception, or `cycle_id` ending up `None`) must never prevent `execute` from running. The durability write is a safety net around the real action, not a gate in front of it -- write the test for this exact property (see Tests, item 3) before writing the implementation, and confirm it fails first for the right reason.

### 3. Startup resume + reconciliation logic

Once step 1 identifies the real entry point, add a function there (exact module TBD by step 1's findings — do not invent a new module for this if an obvious existing one already owns task-startup logic):

```python
async def resume_or_start_attempt(task_id: str, graph_port, api_client) -> dict:
    """Returns {"resumed": bool, "step_index": int, "thread_id": Any | None}.
    On ambiguous crash recovery, reconciles against the REAL API observation
    before returning -- never assumes an action did or didn't happen from
    graph bookkeeping alone."""
    start_or_resume = getattr(graph_port, "start_or_resume_thread", None)
    if start_or_resume is None:
        return {"resumed": False, "step_index": 0, "thread_id": None}

    try:
        result = start_or_resume(anchor_ref=task_id, anchor_type="goal")
    except Exception:
        return {"resumed": False, "step_index": 0, "thread_id": None}

    if not result.get("resumed"):
        return {"resumed": False, "step_index": 0, "thread_id": result.get("thread_id")}

    last_cycle = result.get("last_cycle")
    if last_cycle and last_cycle.get("action_sent") and not last_cycle.get("action_confirmed_by_observation"):
        # Ambiguous window -- ask the real API, never assume.
        real_observation = await api_client.get_current_observation(task_id)  # confirm real method name during step 1
        action_confirmed = _effect_visible_in_observation(last_cycle, real_observation)  # see below
        confirm_cycle = getattr(graph_port, "confirm_cycle", None)
        if confirm_cycle is not None:
            try:
                confirm_cycle(last_cycle.get("cycle_id"), decision="resumed", confirmed=action_confirmed)
            except Exception:
                pass

    return {
        "resumed": True,
        "step_index": int(last_cycle.get("step", 0)) if last_cycle else 0,
        "thread_id": result.get("thread_id"),
    }


def _effect_visible_in_observation(last_cycle: dict, real_observation) -> bool:
    """Best-effort: without a recoverable predicted-effect record on the
    cycle itself, treat any real observation successfully retrieved as
    confirmation that resuming from the CURRENT real state (not a replay)
    is safe -- the goal is never re-send the action on a guess, not to
    perfectly reconstruct the crashed step. Refine this if a predicted
    effect becomes available on the Cycle record in a later card."""
    return real_observation is not None
```

This is intentionally conservative/simple for a first version — confirm with the actual `api_client`'s real method for fetching current game state during step 1, and adjust the function signature to match rather than inventing one.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/workflow.py` | Write-ahead/confirm bracketing around `execute` |
| Real entry point (found in step 1) | `resume_or_start_attempt` + wiring into task startup |
| `tests/test_a204_resume_crash_safety.py` (new) | Coverage, see Tests |

## Tests

`tests/test_a204_resume_crash_safety.py`:

1. `write_cycle` is called with `action_sent=True` before `execute` runs — assert via call-order tracking (e.g. a shared list both mocks append to, assert `write_cycle` appears before `execute`), not just that both were called.
2. `confirm_cycle` is called after `execute` returns, with the `cycle_id` from `write_cycle`'s result.
3. **`write_cycle` raising an exception does not prevent `execute` from running** — assert `execute`'s mock was still called, and `run()` completes normally (this is the "durability write is a safety net, not a gate" invariant — write this test first, confirm it fails against a naive implementation that doesn't wrap the call in try/except, then implement correctly).
4. **Crash-injection, branch A: action actually landed.** Mock `start_or_resume_thread` returning `resumed=True`, `last_cycle={"action_sent": True, "action_confirmed_by_observation": False, "cycle_id": "c1", "step": 3}`. Mock the real API observation to show the action's effect present. Assert `resume_or_start_attempt` calls `confirm_cycle(cycle_id="c1", ..., confirmed=True)` and does NOT trigger any code path that would re-send the action.
5. **Crash-injection, branch B: action never landed.** Same setup, but the real API observation shows no effect. Assert `confirm_cycle` is called with `confirmed=False`, and the resumed state correctly allows a fresh attempt at that step.
6. `resumed=False` (no prior thread) — proceeds as a normal fresh start, no reconciliation logic invoked at all.
7. Graph unreachable at startup (`start_or_resume_thread` raises or `graph_port` lacks the method) — falls back to a normal fresh start, no crash.

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a204_resume_crash_safety.py -v
make test-a
make test-all
```

Live confirmation: best-effort per the card's own acceptance criteria — a real crash-mid-episode is hard to manufacture live; the crash-injection unit tests (items 4-5 above) are the primary correctness evidence for this card, matching how A190-A199 treated their own hardest-to-live-reproduce scenarios.

## Assumptions/defaults

- `_effect_visible_in_observation`'s current implementation is deliberately conservative (any successfully-retrieved real observation counts as "safe to resume from here") rather than attempting precise before/after effect comparison — refining this to compare against a recorded predicted effect is a reasonable future improvement, not required for this card to be complete.
- This card depends on step 1's real-entry-point investigation landing correctly — if the actual call chain differs meaningfully from what's sketched here, prioritize correctness against the real code over matching this plan's sketch exactly, and document the deviation clearly in the Resolution.
