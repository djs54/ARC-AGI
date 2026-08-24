"""Tests for A201 — investigation thread graph client methods.

Tests the four new methods added to ArcGraphQueryPort:
- start_or_resume_thread
- write_thread_state
- write_cycle
- confirm_cycle

Each tests the capability_missing degradation pattern and correct parameter passing.
"""

from unittest.mock import Mock, MagicMock, call
import pytest

from agents.arc4.graph_queries import ArcGraphQueryPort


@pytest.fixture
def mock_brain_client():
    """Create a mock brain client for testing."""
    return Mock()


@pytest.fixture
def graph_port(mock_brain_client):
    """Create an ArcGraphQueryPort instance with a mock brain client."""
    return ArcGraphQueryPort(
        brain_client=mock_brain_client,
        task_id="test_task_123",
        session_id="test_session_456",
        strict=False,
    )


class TestStartOrResumeThread:
    """Tests for start_or_resume_thread method."""

    def test_start_or_resume_thread_capability_missing_degrades_cleanly(self, graph_port, mock_brain_client):
        """Test that capability_missing returns a fresh-thread result, not an error."""
        # Setup mock to return capability_missing
        mock_brain_client.call_tool.return_value = {"status": "capability_missing"}

        # Call the method
        result = graph_port.start_or_resume_thread("entity_5", "entity")

        # Verify it returns the fresh-thread shape, not raising
        assert result == {
            "thread_id": None,
            "state": "exploring",
            "resumed": False,
            "last_cycle": None,
        }

    def test_start_or_resume_thread_parses_resume_payload(self, graph_port, mock_brain_client):
        """Test that a real resume payload is parsed correctly."""
        # Setup mock to return a real resume payload
        mock_brain_client.call_tool.return_value = {
            "thread_id": "thread_abc123",
            "state": "deepening",
            "resumed": True,
            "last_cycle": {
                "cycle_id": "cycle_xyz789",
                "step": 4,
                "action_sent": True,
                "action_confirmed_by_observation": False,
            },
        }

        # Call the method
        result = graph_port.start_or_resume_thread("entity_5", "entity")

        # Verify the payload is parsed through correctly
        assert result["thread_id"] == "thread_abc123"
        assert result["state"] == "deepening"
        assert result["resumed"] is True
        assert result["last_cycle"]["cycle_id"] == "cycle_xyz789"
        assert result["last_cycle"]["step"] == 4


class TestWriteThreadState:
    """Tests for write_thread_state method."""

    def test_write_thread_state_with_none_thread_id_skips_call(self, graph_port, mock_brain_client):
        """Test that thread_id=None returns skipped dict without calling _call_tool."""
        # Call with None thread_id
        result = graph_port.write_thread_state(None, "exploring")

        # Verify it returns skipped status
        assert result == {"status": "skipped", "reason": "no_thread_id"}

        # Verify _call_tool was never invoked
        mock_brain_client.call_tool.assert_not_called()

    def test_write_thread_state_with_real_thread_id_calls_tool(self, graph_port, mock_brain_client):
        """Test that a real thread_id invokes _call_tool with correct payload."""
        # Setup mock to return success
        mock_brain_client.call_tool.return_value = {"status": "ok"}

        # Call the method
        result = graph_port.write_thread_state("thread_abc123", "deepening")

        # Verify _call_tool was called with correct payload
        mock_brain_client.call_tool.assert_called_once()
        call_args = mock_brain_client.call_tool.call_args
        assert call_args[0][0] == "arc_write_thread_state"
        assert call_args[0][1]["thread_id"] == "thread_abc123"
        assert call_args[0][1]["state"] == "deepening"


class TestWriteCycle:
    """Tests for write_cycle method."""

    def test_write_cycle_with_none_thread_id_skips_call(self, graph_port, mock_brain_client):
        """Test that thread_id=None returns cycle_id dict without calling _call_tool."""
        # Call with None thread_id
        result = graph_port.write_cycle(None, 4, True)

        # Verify it returns cycle_id=None
        assert result == {"cycle_id": None}

        # Verify _call_tool was never invoked
        mock_brain_client.call_tool.assert_not_called()

    def test_write_cycle_capability_missing_returns_none(self, graph_port, mock_brain_client):
        """Test that capability_missing returns cycle_id=None."""
        # Setup mock to return capability_missing
        mock_brain_client.call_tool.return_value = {"status": "capability_missing"}

        # Call the method
        result = graph_port.write_cycle("thread_abc123", 4, True)

        # Verify it returns cycle_id=None
        assert result == {"cycle_id": None}

    def test_write_cycle_parses_real_cycle_id(self, graph_port, mock_brain_client):
        """Test that a real cycle_id response is parsed correctly."""
        # Setup mock to return a real cycle_id
        mock_brain_client.call_tool.return_value = {"cycle_id": "cycle_xyz789"}

        # Call the method
        result = graph_port.write_cycle("thread_abc123", 4, True)

        # Verify it parses the cycle_id correctly
        assert result == {"cycle_id": "cycle_xyz789"}


class TestConfirmCycle:
    """Tests for confirm_cycle method."""

    def test_confirm_cycle_with_none_cycle_id_skips_call(self, graph_port, mock_brain_client):
        """Test that cycle_id=None returns skipped dict without calling _call_tool."""
        # Call with None cycle_id
        result = graph_port.confirm_cycle(None, "repeat_deepen", True)

        # Verify it returns skipped status
        assert result == {"status": "skipped", "reason": "no_cycle_id"}

        # Verify _call_tool was never invoked
        mock_brain_client.call_tool.assert_not_called()

    def test_confirm_cycle_with_real_cycle_id_calls_tool(self, graph_port, mock_brain_client):
        """Test that a real cycle_id invokes _call_tool with correct payload."""
        # Setup mock to return success
        mock_brain_client.call_tool.return_value = {"status": "ok"}

        # Call the method
        result = graph_port.confirm_cycle("cycle_xyz789", "advance", True)

        # Verify _call_tool was called with correct payload
        mock_brain_client.call_tool.assert_called_once()
        call_args = mock_brain_client.call_tool.call_args
        assert call_args[0][0] == "arc_confirm_cycle"
        assert call_args[0][1]["cycle_id"] == "cycle_xyz789"
        assert call_args[0][1]["decision"] == "advance"
        assert call_args[0][1]["confirmed"] is True


class TestIntegration:
    """Integration-style tests for the investigation thread workflow."""

    def test_thread_workflow_happy_path(self, graph_port, mock_brain_client):
        """Test a complete workflow: start thread, write state, write cycle, confirm."""
        # Setup mocks
        mock_brain_client.call_tool.side_effect = [
            {"thread_id": "thread_abc123", "state": "exploring", "resumed": False, "last_cycle": None},
            {"status": "ok"},
            {"cycle_id": "cycle_xyz789"},
            {"status": "ok"},
        ]

        # Start/resume thread
        thread = graph_port.start_or_resume_thread("entity_5", "entity")
        assert thread["thread_id"] == "thread_abc123"

        # Write thread state
        state_result = graph_port.write_thread_state(thread["thread_id"], "deepening")
        assert state_result["status"] == "ok"

        # Write cycle
        cycle = graph_port.write_cycle(thread["thread_id"], 4, True)
        assert cycle["cycle_id"] == "cycle_xyz789"

        # Confirm cycle
        confirm = graph_port.confirm_cycle(cycle["cycle_id"], "advance", True)
        assert confirm["status"] == "ok"

        # Verify all calls were made
        assert mock_brain_client.call_tool.call_count == 4

    def test_thread_workflow_all_degraded(self, graph_port, mock_brain_client):
        """Test workflow where all capabilities are missing."""
        # Setup mocks to return capability_missing for all
        mock_brain_client.call_tool.side_effect = [
            {"status": "capability_missing"},
            {"status": "capability_missing"},
            {"status": "capability_missing"},
        ]

        # Start/resume thread (degrades)
        thread = graph_port.start_or_resume_thread("entity_5", "entity")
        assert thread["thread_id"] is None

        # Write thread state (skips because thread_id is None)
        state_result = graph_port.write_thread_state(thread["thread_id"], "deepening")
        assert state_result["status"] == "skipped"

        # Write cycle (skips because thread_id is None)
        cycle = graph_port.write_cycle(thread["thread_id"], 4, True)
        assert cycle["cycle_id"] is None

        # No calls should have been made to write_thread_state or write_cycle
        # because thread_id was None
        assert mock_brain_client.call_tool.call_count == 1
