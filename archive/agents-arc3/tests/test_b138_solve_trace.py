"""Tests for B138: Expose SolveEngine Brain I/O in agent_trace."""

import pytest
from unittest.mock import AsyncMock, MagicMock, call
import asyncio

from agents.arc3.solver import SolveEngine, PlanChunk


@pytest.fixture
def mock_brain():
    """Create a mock brain client."""
    brain = AsyncMock()
    brain.recall_plans = AsyncMock(return_value={"plans": []})
    brain.recall_relevant_lessons = AsyncMock(return_value={"lessons": []})
    brain.register_plan = AsyncMock(return_value={"plan_id": "test_plan_1"})
    brain.analogical_search = AsyncMock(return_value={"results": []})
    brain.trace_event = AsyncMock(return_value=None)
    return brain


@pytest.fixture
def mock_llm():
    """Create a mock LLM client."""
    return MagicMock()


@pytest.fixture
def trace_events():
    """Collect trace events for testing."""
    return []


@pytest.fixture
def emit_trace_callback(trace_events):
    """Create a trace callback that collects events."""
    def callback(event_type: str, operation: str, details=None, result=None, elapsed_ms=None):
        trace_events.append({
            "event_type": event_type,
            "operation": operation,
            "details": details or {},
            "result": result,
            "elapsed_ms": elapsed_ms,
        })
    return callback


@pytest.fixture
def solver_with_trace(mock_brain, mock_llm, emit_trace_callback):
    """Create a SolveEngine instance with trace callback."""
    return SolveEngine(
        brain_client=mock_brain,
        llm_client=mock_llm,
        session_id="test_session",
        emit_trace_event=emit_trace_callback,
    )


@pytest.fixture
def solver_without_trace(mock_brain, mock_llm):
    """Create a SolveEngine instance without trace callback."""
    return SolveEngine(
        brain_client=mock_brain,
        llm_client=mock_llm,
        session_id="test_session",
        emit_trace_event=None,
    )


class TestTraceInitialization:
    """Test trace callback initialization."""

    def test_solver_accepts_trace_callback(self, solver_with_trace):
        """Solver should accept optional trace callback."""
        assert solver_with_trace._emit_trace is not None

    def test_solver_works_without_trace_callback(self, solver_without_trace):
        """Solver should work when trace callback is None."""
        assert solver_without_trace._emit_trace is None

    def test_trace_method_exists(self, solver_with_trace):
        """Solver should have _trace() helper method."""
        assert hasattr(solver_with_trace, "_trace")
        assert callable(solver_with_trace._trace)


class TestTraceHelper:
    """Test the _trace() helper method."""

    def test_trace_emits_event_when_callback_set(self, solver_with_trace, trace_events):
        """_trace() should emit event when callback is registered."""
        solver_with_trace._trace("test_event", "test_op", {"key": "value"})

        assert len(trace_events) == 1
        assert trace_events[0]["event_type"] == "test_event"
        assert trace_events[0]["operation"] == "test_op"
        assert trace_events[0]["details"]["key"] == "value"

    def test_trace_silent_when_no_callback(self, solver_without_trace):
        """_trace() should be silent when callback is None."""
        # Should not raise an exception
        solver_without_trace._trace("test_event", "test_op", {"key": "value"})

    def test_trace_accepts_optional_parameters(self, solver_with_trace, trace_events):
        """_trace() should accept optional result and elapsed_ms."""
        solver_with_trace._trace(
            "test_event",
            "test_op",
            {"step": 1},
            {"count": 5},
            123.45,
        )

        assert len(trace_events) == 1
        assert trace_events[0]["details"]["step"] == 1
        assert trace_events[0]["result"]["count"] == 5
        assert trace_events[0]["elapsed_ms"] == 123.45


@pytest.mark.asyncio
async def test_register_chunk_plan_emits_trace(solver_with_trace, trace_events):
    """_register_chunk_plan should emit trace events."""
    chunk = PlanChunk(
        description="Test chunk",
        estimated_actions=["ACTION1"],
    )

    await solver_with_trace._register_chunk_plan(chunk, step=5)

    # Should have emitted trace events
    trace_ops = [e["operation"] for e in trace_events]
    assert "register_plan" in trace_ops

    # Find the trace events
    start_events = [e for e in trace_events if e["event_type"] == "solve_register_plan"]
    done_events = [e for e in trace_events if e["event_type"] == "solve_register_plan_done"]

    assert len(start_events) == 1
    assert start_events[0]["details"]["step"] == 5
    assert start_events[0]["details"]["plan_type"] == "chunk"

    assert len(done_events) == 1
    assert done_events[0]["details"]["plan_type"] == "chunk"
    assert done_events[0]["result"]["plan_id"] == "test_plan_1"


@pytest.mark.asyncio
async def test_register_solve_plan_emits_trace(solver_with_trace, trace_events):
    """_register_solve_plan should emit trace events."""
    observation = {
        "dataset_id": "training",
        "task_id": "007d8e4d",
    }

    await solver_with_trace._register_solve_plan(observation, step=3)

    # Should have emitted trace events
    register_events = [e for e in trace_events if "register_plan" in e["event_type"]]
    assert len(register_events) > 0

    # Check that trace includes step and plan_type
    for event in register_events:
        if event["event_type"] == "solve_register_plan":
            assert event["details"]["step"] == 3
            assert event["details"]["plan_type"] == "top"


@pytest.mark.asyncio
async def test_register_chunk_plan_without_trace(solver_without_trace, mock_brain):
    """_register_chunk_plan should work without trace callback."""
    chunk = PlanChunk(
        description="Test chunk",
        estimated_actions=["ACTION1"],
    )

    # Should not raise exception
    await solver_without_trace._register_chunk_plan(chunk, step=5)

    # Should still call register_plan
    mock_brain.register_plan.assert_called_once()


@pytest.mark.asyncio
async def test_register_solve_plan_without_trace(solver_without_trace, mock_brain):
    """_register_solve_plan should work without trace callback."""
    observation = {
        "dataset_id": "training",
        "task_id": "007d8e4d",
    }

    # Should not raise exception
    await solver_without_trace._register_solve_plan(observation, step=3)

    # Should still call register_plan
    mock_brain.register_plan.assert_called_once()


def test_trace_event_includes_elapsed_time(solver_with_trace, trace_events):
    """Trace events should include elapsed_ms when measured."""
    solver_with_trace._trace(
        "test_event",
        "test_op",
        {"step": 1},
        {"result": "ok"},
        42.5,
    )

    assert trace_events[0]["elapsed_ms"] == 42.5


def test_trace_event_defaults_missing_params(solver_with_trace, trace_events):
    """Trace events should handle missing optional parameters."""
    solver_with_trace._trace("test_event", "test_op")

    assert trace_events[0]["details"] == {}
    assert trace_events[0]["result"] is None
    assert trace_events[0]["elapsed_ms"] is None
