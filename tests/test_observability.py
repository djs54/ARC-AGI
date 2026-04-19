import pytest
from sidequest_mcp_client.observability import (
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
    obs = Observability({"observability": {"enabled": True}})
    span = obs.span("test", {"key": "val"})
    # Must work without 'with'
    s = span.__enter__()
    assert s is not None
    if hasattr(s, "set_attribute"):
        s.set_attribute("key2", "val2")
    span.__exit__(None, None, None)

def test_preflight_auto_enables_when_packages_present(monkeypatch):
    """A016: auto-enable observability when phoenix/OTEL importable and config does not opt out."""
    from run_single_puzzle import _enforce_observability_preflight
    import importlib.util as _iu
    import os

    monkeypatch.delenv("PHOENIX_ENABLE", raising=False)

    def fake_find_spec(name):
        if name in ("opentelemetry", "phoenix", "phoenix.otel"):
            return object()  # truthy sentinel
        return None

    monkeypatch.setattr(_iu, "find_spec", fake_find_spec)
    # Ensure build_observability used by run_single_puzzle is not a runtime blocker;
    # patch the imported symbol on the entrypoint module so the preflight uses it.
    import run_single_puzzle as _rsp
    monkeypatch.setattr(_rsp, "build_observability", lambda cfg: type("_OK", (), {"enabled": True})())

    # config with no [observability] section
    cfg = {"llm": {"provider": "ollama"}}
    # _enforce_observability_preflight calls build_observability after the auto-enable path;
    # we accept whatever it returns — we only assert that the auto-enable side effects fired.
    try:
        _enforce_observability_preflight(cfg)
    except RuntimeError:
        # build_observability may still raise if Phoenix is unreachable on this test host;
        # that is acceptable — the auto-enable side effects fire BEFORE build_observability.
        pass

    assert os.environ.get("PHOENIX_ENABLE") == "1"
    assert cfg["observability"]["enabled"] is True


def test_preflight_respects_explicit_disable(monkeypatch):
    """A016: an explicit [observability] enabled = false must not flip PHOENIX_ENABLE."""
    from run_single_puzzle import _enforce_observability_preflight
    import os
    monkeypatch.delenv("PHOENIX_ENABLE", raising=False)

    cfg = {"observability": {"enabled": False}}
    _enforce_observability_preflight(cfg)

    assert "PHOENIX_ENABLE" not in os.environ
    assert cfg["observability"]["enabled"] is False


def test_ensure_contract_fields_fills_defaults():
    attrs = {"session_id": "s1", "task_id": "t1", "step": 1, "phase": "act"}
    out = ensure_contract_fields(attrs, REQUIRED_DECISION_FIELDS, defaults={"action_id": "unknown"})
    assert out["action_id"] == "unknown"
    assert out["session_id"] == "s1"


def test_preflight_auto_enable_soft_fails_on_phoenix_unreachable(monkeypatch):
    """A022: auto-enable path must not raise when Phoenix cannot initialize."""
    import run_single_puzzle as rsp
    import importlib.util as _iu
    import os

    monkeypatch.delenv("PHOENIX_ENABLE", raising=False)

    def fake_find_spec(name):
        if name in ("opentelemetry", "phoenix", "phoenix.otel"):
            return object()
        return None

    monkeypatch.setattr(_iu, "find_spec", fake_find_spec)

    class _Broken:
        enabled = False

    monkeypatch.setattr(rsp, "build_observability", lambda cfg: _Broken())

    cfg = {"llm": {}}
    # Should not raise when auto-enabled and build_observability reports disabled
    rsp._enforce_observability_preflight(cfg)

    assert cfg.get("observability", {}).get("enabled") is False
    assert "PHOENIX_ENABLE" not in os.environ


def test_preflight_explicit_enable_still_hard_fails(monkeypatch):
    """A022: an explicit PHOENIX_ENABLE=1 must still raise on Phoenix failure."""
    from run_single_puzzle import _enforce_observability_preflight
    import sidequest_mcp_client.observability as obs_mod
    import importlib.util as _iu

    # Ensure find_spec does not raise ModuleNotFoundError during probing; missing
    # packages will be reported via the 'missing' list and cause a RuntimeError.
    monkeypatch.setattr(_iu, "find_spec", lambda name: None)

    monkeypatch.setenv("PHOENIX_ENABLE", "1")

    class _Broken:
        enabled = False

    monkeypatch.setattr(obs_mod, "build_observability", lambda cfg: _Broken())

    cfg = {"observability": {"enabled": True}}
    with pytest.raises(RuntimeError):
        _enforce_observability_preflight(cfg)

