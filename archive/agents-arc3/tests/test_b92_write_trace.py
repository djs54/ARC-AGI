"""Tests for B92 - ARC Step-Level SideQuests Write Trace."""

import pytest
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.orchestrator import ARCOrchestrator


@pytest.fixture
def mock_orchestrator():
    """Create an ARCOrchestrator with mocked dependencies."""
    brain_client = AsyncMock()
    llm_client = AsyncMock()
    serializer = MagicMock()
    serializer._estimate_tokens.return_value = 100

    orch = ARCOrchestrator(
        brain_client=brain_client,
        llm_client=llm_client,
        session_id="test-session",
        serializer=serializer,
        config={},
    )
    return orch


def test_write_trace_context_starts_as_bootstrap(mock_orchestrator):
    """B92: Write trace context should default to 'bootstrap'."""
    assert mock_orchestrator._write_trace_context == "bootstrap"


def test_set_write_trace_context(mock_orchestrator):
    """B92: set_write_trace_context should update context."""
    mock_orchestrator.set_write_trace_context("step-1")
    assert mock_orchestrator._write_trace_context == "step-1"


def test_record_write_event_basic(mock_orchestrator):
    """B92: _record_write_event should create event dict with required fields."""
    mock_orchestrator._record_write_event(
        kind="notify_turn",
        summary="Test summary",
    )

    assert len(mock_orchestrator._write_trace) == 1
    event = mock_orchestrator._write_trace[0]
    assert event["kind"] == "notify_turn"
    assert event["type"] == "notify_turn"
    assert event["summary"] == "Test summary"
    assert event["phase"] == "bootstrap"
    assert event["status"] == "ok"


def test_record_write_event_with_status(mock_orchestrator):
    """B92: _record_write_event should capture status from response_dict."""
    response_dict = {"status": "queued"}
    mock_orchestrator._record_write_event(
        kind="notify_turn",
        summary="Test",
        response_dict=response_dict,
    )

    event = mock_orchestrator._write_trace[0]
    assert event["status"] == "queued"


def test_record_write_event_with_detail(mock_orchestrator):
    """B92: _record_write_event should include detail when provided."""
    detail = {"role": "user", "scope": "step_observation"}
    mock_orchestrator._record_write_event(
        kind="hypothesis_update",
        summary="ACTION1 -> tentative",
        detail=detail,
    )

    event = mock_orchestrator._write_trace[0]
    assert event["detail"] == detail


def test_record_write_event_with_source_step(mock_orchestrator):
    """B92: _record_write_event should include source_step when provided."""
    mock_orchestrator._record_write_event(
        kind="hypothesis_update",
        summary="Test",
        source_step=5,
    )

    event = mock_orchestrator._write_trace[0]
    assert event["source_step"] == 5


def test_consume_write_trace_returns_list(mock_orchestrator):
    """B92: consume_write_trace should return list of events."""
    mock_orchestrator._record_write_event(kind="notify_turn", summary="Test 1")
    mock_orchestrator._record_write_event(kind="hypothesis_update", summary="Test 2")

    trace = mock_orchestrator.consume_write_trace()
    assert len(trace) == 2
    assert trace[0]["kind"] == "notify_turn"
    assert trace[1]["kind"] == "hypothesis_update"


def test_consume_write_trace_clears_ledger(mock_orchestrator):
    """B92: consume_write_trace should clear the ledger after consuming."""
    mock_orchestrator._record_write_event(kind="notify_turn", summary="Test")

    trace1 = mock_orchestrator.consume_write_trace()
    assert len(trace1) == 1

    trace2 = mock_orchestrator.consume_write_trace()
    assert len(trace2) == 0


def test_write_trace_includes_tentative_status(mock_orchestrator):
    """B92: Write trace detail should distinguish tentative vs confirmed."""
    detail = {
        "saved_action_facts": [
            {
                "action": "ACTION1",
                "value_status": "tentative",
                "description": "Tentative effect"
            }
        ]
    }
    mock_orchestrator._record_write_event(
        kind="hypothesis_update",
        summary="ACTION1 -> tentative",
        detail=detail,
    )

    event = mock_orchestrator._write_trace[0]
    assert event["detail"]["saved_action_facts"][0]["value_status"] == "tentative"


def test_write_trace_with_confirmed_status(mock_orchestrator):
    """B92: Write trace should support confirmed status."""
    detail = {
        "saved_action_facts": [
            {
                "action": "ACTION2",
                "value_status": "confirmed",
                "description": "Confirmed effect"
            }
        ]
    }
    mock_orchestrator._record_write_event(
        kind="hypothesis_update",
        summary="ACTION2 -> confirmed",
        detail=detail,
    )

    event = mock_orchestrator._write_trace[0]
    assert event["detail"]["saved_action_facts"][0]["value_status"] == "confirmed"


def test_write_trace_with_path_hypotheses(mock_orchestrator):
    """B92: Write trace should include path hypotheses when present."""
    detail = {
        "saved_action_facts": [],
        "saved_path_hypotheses": [
            {
                "actions": ["ACTION1", "ACTION2"],
                "value_status": "tentative",
            }
        ]
    }
    mock_orchestrator._record_write_event(
        kind="hypothesis_update",
        summary="Path found",
        detail=detail,
    )

    event = mock_orchestrator._write_trace[0]
    assert len(event["detail"]["saved_path_hypotheses"]) == 1
    assert event["detail"]["saved_path_hypotheses"][0]["actions"] == ["ACTION1", "ACTION2"]


def test_compact_text_truncates_long_summaries(mock_orchestrator):
    """B92: _compact_text should truncate summaries longer than 180 chars."""
    long_text = "x" * 200
    compact = mock_orchestrator._compact_text(long_text)

    assert len(compact) <= 181  # 180 - 1 + "…"
    assert compact.endswith("…")


def test_multiple_write_traces_per_context(mock_orchestrator):
    """B92: Multiple write events can be recorded in same context."""
    mock_orchestrator.set_write_trace_context("step-1")

    mock_orchestrator._record_write_event(kind="notify_turn", summary="Observation")
    mock_orchestrator._record_write_event(kind="hypothesis_update", summary="Hypothesis")

    trace = mock_orchestrator.consume_write_trace()
    assert len(trace) == 2
    assert all(e["phase"] == "step-1" for e in trace)


def test_write_trace_context_persists_across_events(mock_orchestrator):
    """B92: Write trace context should apply to all events until changed."""
    mock_orchestrator.set_write_trace_context("step-1")
    mock_orchestrator._record_write_event(kind="event_1", summary="Test")

    mock_orchestrator.set_write_trace_context("step-2")
    mock_orchestrator._record_write_event(kind="event_2", summary="Test")

    trace = mock_orchestrator.consume_write_trace()
    assert trace[0]["phase"] == "step-1"
    assert trace[1]["phase"] == "step-2"
