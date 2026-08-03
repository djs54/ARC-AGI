import json
import time
from unittest.mock import patch

import pytest

from agents.arc3.circuit_breaker import CircuitBreakerLLMClient, CircuitState
from agents.arc3.orchestrator import ARCOrchestrator
from benchmarks.arc3.adapter import NoOpBrainClient
from benchmarks.arc3.state_serializer import StateSerializerForARC


class DummyLLM:
    def __init__(self, responses=None, *, model_name: str = "dummy-model"):
        self._responses = list(responses or ['{"action_id": "ACTION1", "rationale": "ok"}'])
        self.calls = 0
        self.model_name = model_name
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}

    def chat(self, messages, **kwargs):
        self.calls += 1
        if self._responses:
            value = self._responses.pop(0)
        else:
            value = '{"action_id": "ACTION1", "rationale": "ok"}'
        if isinstance(value, Exception):
            raise value
        return value


def test_closed_state_success_stays_closed():
    inner = DummyLLM(['{"action_id": "ACTION1", "rationale": "ok"}'])
    breaker = CircuitBreakerLLMClient(inner)

    result = breaker.chat([{"role": "user", "content": "hi"}])

    assert json.loads(result)["action_id"] == "ACTION1"
    assert breaker.state is CircuitState.CLOSED
    assert breaker.consecutive_failures == 0
    assert inner.calls == 1


def test_three_failures_open_circuit():
    inner = DummyLLM([TimeoutError("timed out"), TimeoutError("timed out"), TimeoutError("timed out")])
    breaker = CircuitBreakerLLMClient(inner, failure_threshold=3, max_retries=0)

    for _ in range(3):
        result = breaker.chat([{"role": "user", "content": "hi"}])

    assert breaker.state is CircuitState.OPEN
    assert breaker.consecutive_failures == 3
    assert json.loads(result)["rationale"] == "circuit breaker fallback"


def test_open_state_short_circuits_without_inner_call():
    inner = DummyLLM()
    breaker = CircuitBreakerLLMClient(inner, max_retries=0)
    breaker._state = CircuitState.OPEN
    breaker._last_failure_time = time.time()

    before = inner.calls
    result = breaker.chat([{"role": "user", "content": "hi"}])

    assert inner.calls == before
    assert breaker.state is CircuitState.OPEN
    assert json.loads(result)["action_id"] == "ACTION1"


def test_cooldown_allows_half_open_probe_and_success_closes_circuit():
    inner = DummyLLM(['{"action_id": "ACTION1", "rationale": "recovered"}'])
    events = []

    def emit_trace(event_type, operation, details=None, result=None, elapsed_ms=None):
        events.append((event_type, operation, details or {}))

    breaker = CircuitBreakerLLMClient(inner, cooldown_seconds=30.0, emit_trace_event=emit_trace)
    breaker._state = CircuitState.OPEN
    breaker._last_failure_time = time.time() - 31.0
    breaker._consecutive_failures = 3

    result = breaker.chat([{"role": "user", "content": "probe"}])

    assert json.loads(result)["rationale"] == "recovered"
    assert breaker.state is CircuitState.CLOSED
    transitions = [details for _, operation, details in events if operation == "llm_circuit_breaker_transition"]
    assert any(t["from"] == "open" and t["to"] == "half_open" for t in transitions)
    assert any(t["from"] == "half_open" and t["to"] == "closed" for t in transitions)


def test_half_open_failure_reopens_circuit():
    inner = DummyLLM([RuntimeError("ollama unavailable")])
    breaker = CircuitBreakerLLMClient(inner, cooldown_seconds=30.0, max_retries=0)
    breaker._state = CircuitState.OPEN
    breaker._last_failure_time = time.time() - 31.0
    breaker._consecutive_failures = 3

    breaker.chat([{"role": "user", "content": "probe"}])

    assert breaker.state is CircuitState.OPEN
    assert breaker.consecutive_failures == 4


def test_exponential_backoff_uses_1_2_4_seconds():
    inner = DummyLLM([
        TimeoutError("1"),
        TimeoutError("2"),
        TimeoutError("3"),
        TimeoutError("4"),
    ])
    breaker = CircuitBreakerLLMClient(inner, failure_threshold=99, max_retries=3)

    with patch("agents.arc3.circuit_breaker.time.sleep") as mock_sleep:
        breaker.chat([{"role": "user", "content": "retry"}])

    assert [call.args[0] for call in mock_sleep.call_args_list] == [1.0, 2.0, 4.0]
    assert inner.calls == 4


def test_attribute_proxy_exposes_inner_client_attributes():
    inner = DummyLLM(model_name="llama3.1:8b")
    breaker = CircuitBreakerLLMClient(inner)

    assert breaker.model_name == "llama3.1:8b"


def test_typeerror_passthrough_preserves_response_format_fallback():
    inner = DummyLLM([TypeError("unexpected keyword argument 'response_format'")])
    breaker = CircuitBreakerLLMClient(inner, max_retries=3)

    with pytest.raises(TypeError):
        breaker.chat([{"role": "user", "content": "hi"}], response_format={"type": "json_object"})


def test_orchestrator_wraps_llm_with_circuit_breaker():
    inner = DummyLLM()
    orch = ARCOrchestrator(
        brain_client=NoOpBrainClient(),
        llm_client=inner,
        session_id="session-1",
        serializer=StateSerializerForARC(),
        config={"llm": {}},
    )

    assert isinstance(orch.llm, CircuitBreakerLLMClient)
    assert orch.solve_engine.llm is orch.llm
