"""Pure cycle-policy functions shared by inline and Temporal orchestrators.

Temporal-sandbox-safe: stdlib only, deterministic, no I/O.
"""

from __future__ import annotations

from typing import MutableMapping


def check_budget(step_index: int, max_cycles: int) -> str | None:
    """Return budget_exhausted when the cycle budget is spent."""
    if step_index >= max_cycles:
        return "budget_exhausted"
    return None


def check_stall(
    consecutive_no_progress: int,
    max_consecutive_no_progress: int,
    num_available_actions: int,
    num_attempted_actions: int,
) -> str | None:
    """Return stall_detected once all actions are repeatedly non-productive."""
    if consecutive_no_progress < max_consecutive_no_progress:
        return None
    num_available = num_available_actions or 1
    if num_available_actions > 0 and num_available - num_attempted_actions > 0:
        return None
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
    "check_budget",
    "check_stall",
    "record_evaluation_outcome",
    "termination_from_evaluation",
]
