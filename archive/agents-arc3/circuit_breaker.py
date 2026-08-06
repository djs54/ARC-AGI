"""Circuit-breaker wrapper for ARC LLM calls."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerLLMClient:
    """Protect LLM calls with retries, backoff, and fail-fast behavior.

    The ARC orchestrator mostly uses synchronous ``chat()`` calls via
    ``asyncio.to_thread(...)``. This wrapper keeps the same surface area while
    preventing transient provider failures from crashing the puzzle.
    """

    def __init__(
        self,
        inner_client: Any,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        max_retries: int = 3,
        emit_trace_event: Callable[..., Any] | None = None,
    ):
        self._inner = inner_client
        self._failure_threshold = max(1, int(failure_threshold))
        self._cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._max_retries = max(0, int(max_retries))
        self._emit_trace_event = emit_trace_event

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._last_failure_time = 0.0
        self.last_usage: dict | None = None

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def _trace(self, event_type: str, operation: str, details: dict | None = None) -> None:
        if self._emit_trace_event is not None:
            try:
                self._emit_trace_event(event_type, operation, details or {})
            except Exception:
                logger.debug("Circuit-breaker trace callback failed", exc_info=True)

    def _transition_state(self, new_state: CircuitState, reason: str, **details: Any) -> None:
        old_state = self._state
        if new_state == old_state:
            return
        self._state = new_state
        payload = {"from": old_state.value, "to": new_state.value, "reason": reason}
        payload.update(details)
        logger.warning("B184: LLM circuit breaker %s -> %s (%s)", old_state.value, new_state.value, reason)
        self._trace("operation", "llm_circuit_breaker_transition", payload)

    @staticmethod
    def _safe_default_response() -> str:
        return json.dumps(
            {
                "action_id": "ACTION1",
                "rationale": "circuit breaker fallback",
                "approved": True,
                "reason": None,
            }
        )

    @staticmethod
    def _zero_usage() -> dict:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _safe_default_for(self, method_name: str):
        self.last_usage = self._zero_usage()
        if method_name == "chat_with_usage":
            return self._safe_default_response(), self._zero_usage()
        return self._safe_default_response()

    @staticmethod
    def _should_reraise(exc: Exception) -> bool:
        # The orchestrator intentionally catches TypeError to retry without
        # response_format for mock/minimal LLM clients. Preserve that behavior.
        return isinstance(exc, TypeError)

    def _before_request(self, method_name: str):
        if self._inner is None:
            self._trace("warning", "llm_circuit_breaker_missing_client", {"state": self._state.value})
            return self._safe_default_for(method_name)

        if self._state == CircuitState.OPEN:
            elapsed = time.time() - self._last_failure_time
            if elapsed >= self._cooldown_seconds:
                self._transition_state(CircuitState.HALF_OPEN, "cooldown_elapsed")
            else:
                remaining = max(0.0, self._cooldown_seconds - elapsed)
                self._trace(
                    "warning",
                    "llm_circuit_breaker_short_circuit",
                    {"state": self._state.value, "cooldown_remaining_seconds": round(remaining, 2)},
                )
                return self._safe_default_for(method_name)

        return None

    def _call_inner(self, method_name: str, *args, **kwargs):
        method = getattr(self._inner, method_name, None)
        if method is None:
            if method_name == "chat_with_usage":
                content = self._inner.chat(*args, **kwargs)
                usage = getattr(self._inner, "last_usage", None) or self._zero_usage()
                return content, usage
            return self._inner.chat(*args, **kwargs)
        return method(*args, **kwargs)

    def _on_success(self) -> None:
        self._consecutive_failures = 0
        if self._state != CircuitState.CLOSED:
            self._transition_state(CircuitState.CLOSED, "probe_succeeded")

    def _on_failure(self, exc: Exception) -> None:
        self._consecutive_failures += 1
        self._last_failure_time = time.time()
        details = {
            "error_type": type(exc).__name__,
            "message": str(exc),
            "consecutive_failures": self._consecutive_failures,
            "failure_threshold": self._failure_threshold,
        }
        self._trace("warning", "llm_circuit_breaker_failure", details)

        if self._state == CircuitState.HALF_OPEN:
            self._transition_state(CircuitState.OPEN, "half_open_probe_failed", **details)
        elif self._consecutive_failures >= self._failure_threshold:
            self._transition_state(CircuitState.OPEN, "failure_threshold_reached", **details)

    def _call_with_circuit(self, method_name: str, *args, **kwargs):
        fallback = self._before_request(method_name)
        if fallback is not None:
            return fallback

        total_attempts = self._max_retries + 1
        last_exc: Exception | None = None

        for attempt in range(total_attempts):
            try:
                result = self._call_inner(method_name, *args, **kwargs)
                if method_name == "chat_with_usage" and isinstance(result, tuple) and len(result) == 2:
                    self.last_usage = result[1]
                else:
                    self.last_usage = getattr(self._inner, "last_usage", None)
                self._on_success()
                return result
            except Exception as exc:
                if self._should_reraise(exc):
                    raise

                last_exc = exc
                if attempt < total_attempts - 1:
                    backoff = float(2**attempt)
                    self._trace(
                        "warning",
                        "llm_circuit_breaker_retry",
                        {
                            "attempt": attempt + 1,
                            "max_retries": self._max_retries,
                            "backoff_seconds": backoff,
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        },
                    )
                    time.sleep(backoff)

        assert last_exc is not None
        logger.warning("B184: LLM call failed after retries: %s", last_exc)
        self._on_failure(last_exc)
        return self._safe_default_for(method_name)

    def chat(self, messages: list[dict], **kwargs) -> str:
        return self._call_with_circuit("chat", messages, **kwargs)

    def chat_with_usage(self, messages: list[dict], **kwargs) -> tuple[str, dict]:
        return self._call_with_circuit("chat_with_usage", messages, **kwargs)

    async def achat(self, messages: list[dict], **kwargs) -> str:
        return await asyncio.to_thread(self.chat, messages, **kwargs)

    async def achat_with_usage(self, messages: list[dict], **kwargs) -> tuple[str, dict]:
        return await asyncio.to_thread(self.chat_with_usage, messages, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


__all__ = ["CircuitState", "CircuitBreakerLLMClient"]
