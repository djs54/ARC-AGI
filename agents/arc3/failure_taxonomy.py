"""Failure classification helpers for ARC runner outcomes."""

from __future__ import annotations

from enum import Enum


class FailureTaxonomy(str, Enum):
    """Stable categories for puzzle failures.

    These values are exported into benchmark results so downstream metrics can
    distinguish infrastructure failures from reasoning failures.
    """

    LLM_TIMEOUT = "llm_timeout"
    TOOL_TIMEOUT = "tool_timeout"
    WALL_CLOCK_TIMEOUT = "wall_clock_budget_exhausted"
    LLM_PARSE_ERROR = "llm_parse_error"
    API_ERROR = "api_error"
    BUDGET_EXCEEDED = "budget_exceeded"
    STRATEGY_EXHAUSTED = "strategy_exhausted"
    COVERAGE_SATURATED_ABORT = "coverage_saturated_abort"
    STUCK_IN_LOOP = "stuck_in_loop"
    MAX_STEPS_REACHED = "max_steps_reached"
    TERMINAL_STALL = "terminal_stall"
    CRASH = "crash"


# Backward-compatible alias used in the draft plan.
FailureClass = FailureTaxonomy


def classify_failure(
    exc: BaseException | None = None,
    *,
    final_state: str | None = None,
    error_message: str | None = None,
    no_progress_steps: int = 0,
    budget_exhausted: bool = False,
    max_steps_reached: bool = False,
    loop_detected: bool = False,
    graduation_reason: str | None = None,
    coverage_saturated: bool = False,
    plateau_escalation_required: bool = False,
    wall_clock_timeout: bool = False,
) -> FailureTaxonomy:
    """Return the best-effort taxonomy bucket for a failed run.

    This helper is intentionally defensive and never raises. Unknown cases fall
    back to ``FailureTaxonomy.CRASH``.
    """
    try:
        message_parts = []
        if error_message:
            message_parts.append(str(error_message))
        if exc is not None:
            message_parts.append(str(exc))
            message_parts.append(type(exc).__name__)
        if final_state:
            message_parts.append(str(final_state))
        if graduation_reason:
            message_parts.append(str(graduation_reason))
        if plateau_escalation_required:
            message_parts.append("plateau_escalation_required")
        haystack = " | ".join(message_parts).lower()

        # A015 + A018: detect when we learned everything but still couldn't reach goal.
        # This is prioritized over budget/timeout/crash because it's a structural 
        # environment-capacity signal.
        is_saturated = coverage_saturated or (graduation_reason and "coverage_saturated" in graduation_reason.lower())
        
        if is_saturated or (plateau_escalation_required and is_saturated):
            return FailureTaxonomy.COVERAGE_SATURATED_ABORT

        if wall_clock_timeout or "wall-clock" in haystack or "wall clock" in haystack:
            return FailureTaxonomy.WALL_CLOCK_TIMEOUT

        if budget_exhausted or (
            "budget" in haystack and ("exhaust" in haystack or "exceed" in haystack)
        ):
            return FailureTaxonomy.BUDGET_EXCEEDED

        # A058: Detect terminal stall (broad coverage but no levels completed)
        if "terminal_stall" in haystack or "zero terminal progress" in haystack:
            return FailureTaxonomy.TERMINAL_STALL

        if max_steps_reached or "max attempts reached" in haystack or "max steps" in haystack:
            if loop_detected or no_progress_steps >= 20 or "loop" in haystack:
                return FailureTaxonomy.STUCK_IN_LOOP
            return FailureTaxonomy.MAX_STEPS_REACHED

        # A091: Classify HTTP bridge transport errors as tool_timeout, not llm_timeout
        if any(token in haystack for token in (
            "daemon_http_error", "daemon_offline", "cannot reach http",
            "daemon_connection_failed", "mcp_transport_error", "connection refused",
            "connection reset"
        )):
            return FailureTaxonomy.TOOL_TIMEOUT

        # A056/A064: High-specificity tool/API errors before generic timeout
        if any(token in haystack for token in (
            "tools/call:", "mcptimeouterror", "tool_timeout", "memory_timeout", "daemon_timeout"
        )):
            return FailureTaxonomy.TOOL_TIMEOUT

        if any(token in haystack for token in (
            "api_error", "ratelimit", "quota exceeded", "overloaded",
            "internal_server_error", "bad_gateway", "service_unavailable"
        )):
            return FailureTaxonomy.API_ERROR

        if any(token in haystack for token in (
            "timeout",
            "timed out",
            "deadline exceeded",
            "readtimeout",
            "connecttimeout",
            "apitimeouterror",
        )):
            return FailureTaxonomy.LLM_TIMEOUT

        if any(token in haystack for token in (
            "jsondecodeerror",
            "parse error",
            "could not parse",
            "failed to parse",
            "malformed json",
            "invalid json",
            "unparseable",
            "expecting value",
        )):
            return FailureTaxonomy.LLM_PARSE_ERROR

        if any(token in haystack for token in (
            "client error",
            "server error",
            "http error",
            "api error",
            "bad request",
            " 400",
            " 401",
            " 403",
            " 404",
            " 429",
            " 500",
            " 502",
            " 503",
            " 504",
            "/api/",
        )):
            return FailureTaxonomy.API_ERROR

        if loop_detected or "loop detected" in haystack or "state loop" in haystack:
            return FailureTaxonomy.STUCK_IN_LOOP

        if exc is None:
            return FailureTaxonomy.STRATEGY_EXHAUSTED

        return FailureTaxonomy.CRASH
    except Exception:
        return FailureTaxonomy.CRASH


__all__ = ["FailureTaxonomy", "FailureClass", "classify_failure"]
