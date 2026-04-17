import pytest
from mcp_engine.observability import (
    REQUIRED_DECISION_FIELDS,
    build_observability,
    canonical_span_name,
    Observability,
    ensure_contract_fields,
)
from benchmarks.arc3.adapter import LedgerBrainClient
from unittest.mock import MagicMock

def test_observability_disabled_by_default():
    obs = build_observability({})
    assert obs.enabled is False
    with obs.span("test", {}) as span:
        # nullcontext returns None or the context object itself depending on version, 
        # but in our impl we return nullcontext() which returns None on __enter__
        pass

def test_span_manual_enter_exit():
    obs = Observability(enabled=True)
    span = obs.span("test", {"key": "val"})
    # Must work without 'with'
    s = span.__enter__()
    assert s is not None
    if hasattr(s, "set_attribute"):
        s.set_attribute("key2", "val2")
    span.__exit__(None, None, None)

def test_redaction_logic():
    from mcp_engine.observability import _redact_value
    # Sensitive
    val = _redact_value("api_key", "secret123")
    assert "[redacted]" in val
    assert "secret123" not in val
    
    # Bulky
    assert "[101 chars]" == _redact_value("grid", "x" * 101)
    
    # Normal
    assert "hello" == _redact_value("name", "hello")

def test_ledger_brain_client_accepts_observability():
    from benchmarks.arc3.adapter import LedgerBrainClient
    from unittest.mock import MagicMock
    inner = MagicMock()
    obs = build_observability({})
    client = LedgerBrainClient(
        inner=inner,
        ledger=[],
        step_provider=lambda: 0,
        observability=obs,
    )
    assert client.observability == obs

def test_canonical_span_name():
    assert canonical_span_name("test") == "sidequests.test"


def test_ensure_contract_fields_fills_defaults():
    attrs = {"session_id": "s1", "task_id": "t1", "step": 1, "phase": "act"}
    out = ensure_contract_fields(attrs, REQUIRED_DECISION_FIELDS, defaults={"action_id": "unknown"})
    assert out["action_id"] == "unknown"
    assert out["session_id"] == "s1"


def test_ensure_contract_fields_strict_raises():
    with pytest.raises(ValueError):
        ensure_contract_fields({"session_id": "s1"}, REQUIRED_DECISION_FIELDS, strict=True)
