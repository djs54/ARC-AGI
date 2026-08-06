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
    num_attempted_actions: int,
) -> str | None:
    """Return stall_detected once all actions are repeatedly non-productive."""
    num_available = num_available_actions or 1
    if num_available_actions > 0 and num_available - num_attempted_actions > 0:
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
) -> int:
    """Update falsification counts in place; return new no-progress count."""
    if meaningful_progress:
        return 0
    falsification_counts[action_key] = falsification_counts.get(action_key, 0) + max(1, falsification_delta)
    return no_progress_count + 1


def termination_from_evaluation(decision: str | None, reason: str | None) -> tuple[str, str] | None:
    """Map evaluator decision to terminal status tuple, else None."""
    if str(decision or "").lower() == "terminate":
        return ("terminated", reason or "terminated")
    return None


__all__ = [
    "base_action",
    "count_base_actions",
    "check_budget",
    "check_stall",
    "stall_threshold",
    "record_evaluation_outcome",
    "termination_from_evaluation",
]
