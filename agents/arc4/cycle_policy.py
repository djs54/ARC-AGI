"""Pure cycle-policy functions shared by inline and Temporal orchestrators.

Temporal-sandbox-safe: stdlib only, deterministic, no I/O.
"""

from __future__ import annotations

from typing import Iterable, MutableMapping


def base_action(action_key: str) -> str:
    """Collapse a coordinate-targeted action key to its base action.

    ACTION6 click targets are bookkept per-coordinate as ``ACTION6@x,y`` so
    different targets are scored independently. For action-space exhaustion the
    family is what matters, so ``ACTION6@10,20`` and ``ACTION6@30,40`` both
    count as the single base action ``ACTION6``.
    """
    return str(action_key).split("@", 1)[0]


def count_base_actions(attempt_keys: Iterable[str]) -> int:
    """Count distinct base actions among attempt keys (targets collapsed)."""
    return len({base_action(key) for key in attempt_keys})


def untested_remaining_actions(available_actions: Iterable[str], attempt_keys: Iterable[str]) -> int:
    """Count of currently-available base actions never attempted (at any point).

    A248: a set difference between *this cycle's* available_actions and the
    base actions represented in attempt_keys -- not a length subtraction
    against attempt_keys's raw count. attempt_keys (state.action_attempt_
    counts) accumulates for the whole episode and is never reset, so it can
    (and does) hold stale entries for actions no longer in the current
    action space once the environment moves to a differently-composed
    phase (e.g. a probe-phase ACTION6 click that isn't available once play
    becomes goal-directed). The old `len(available) - count_base_actions
    (attempted)` subtraction could go negative in that case and, worse,
    could mask a genuinely-untested current action behind an unrelated
    stale one -- this is always >= 0 by construction and immune to both.
    """
    attempted_base_actions = {base_action(key) for key in attempt_keys}
    return len(set(available_actions) - attempted_base_actions)


def check_budget(step_index: int, max_cycles: int) -> str | None:
    """Return budget_exhausted when the cycle budget is spent."""
    if step_index >= max_cycles:
        return "budget_exhausted"
    return None


def stall_threshold(max_consecutive_no_progress: int, num_available_actions: int) -> int:
    """Effective consecutive-no-progress threshold check_stall gates on.

    Both max_consecutive_no_progress (a fixed floor) and num_available*2 (a
    coverage-scaled ceiling) must be satisfied for a stall to fire -- the
    real threshold is whichever is larger. Exists as its own function so
    diagnostic logging (workflow.py's STALL_CHECK line) can report the same
    number check_stall actually gates on, instead of duplicating half the
    formula and drifting out of sync with it.
    """
    return max(max_consecutive_no_progress, (num_available_actions or 1) * 2)


def check_stall(
    consecutive_no_progress: int,
    max_consecutive_no_progress: int,
    num_available_actions: int,
    num_untested_remaining: int,
) -> str | None:
    """Return stall_detected once all actions are repeatedly non-productive.

    A248: the 4th param is now the caller's already-correctly-scoped
    "genuinely untested in the *current* action space" count (see
    untested_remaining_actions above), not a whole-episode-cumulative
    attempted count this function used to subtract from num_available
    itself. That internal subtraction compared two differently-scoped
    numbers (a point-in-time available-action set vs. a cumulative,
    never-reset attempt count) and could go negative, silently skipping
    this early-return even when the *current* phase's actions genuinely
    hadn't all been tried yet.
    """
    if num_available_actions > 0 and num_untested_remaining > 0:
        return None
    if consecutive_no_progress >= stall_threshold(max_consecutive_no_progress, num_available_actions):
        return "stall_detected"
    return None


def record_evaluation_outcome(
    *,
    no_progress_count: int,
    falsification_counts: MutableMapping[str, int],
    action_key: str,
    meaningful_progress: bool,
    falsification_delta: int,
    count_toward_no_progress: bool = True,
) -> int:
    """Update falsification counts in place; return new no-progress count.

    A242: ``count_toward_no_progress=False`` is set by readiness-probe-phase
    call sites (workflow.py's ``if probe_candidate is not None:`` block,
    including an A241-granted resumed probe window -- both re-enter the same
    code path, so this scoping covers both automatically). A probe click's
    falsification history is real and still accumulates into
    ``falsification_counts`` exactly as before; only the no-progress count
    itself -- the signal goal_resolver.py::_should_escalate_to_llm's
    ``under_confident`` branch reads to ask "has GOAL-DIRECTED play
    repeatedly failed" -- is left unchanged rather than incremented, since
    exploratory probe actions are not goal-directed attempts and essentially
    never register real progress (see backlog/A242.md). Mirrors A230's own
    precedent of scoping ``annatar_unproductive_anchor_streak`` to non-probe
    cycles. A genuine ``meaningful_progress=True`` still resets to 0
    regardless of this flag -- real progress is real progress wherever it
    happens.
    """
    if meaningful_progress:
        return 0
    falsification_counts[action_key] = falsification_counts.get(action_key, 0) + max(1, falsification_delta)
    if not count_toward_no_progress:
        return no_progress_count
    return no_progress_count + 1


def termination_from_evaluation(decision: str | None, reason: str | None) -> tuple[str, str] | None:
    """Map evaluator decision to terminal status tuple, else None."""
    if str(decision or "").lower() == "terminate":
        return ("terminated", reason or "terminated")
    return None


__all__ = [
    "base_action",
    "count_base_actions",
    "untested_remaining_actions",
    "check_budget",
    "check_stall",
    "stall_threshold",
    "record_evaluation_outcome",
    "termination_from_evaluation",
]
