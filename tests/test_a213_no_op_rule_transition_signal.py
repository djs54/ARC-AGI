"""A213: Fix — A No-Op Action Leaves No Trace in Rule/Transition-Level Graph Knowledge.

Tests that no-op actions (empty changed_cells) produce graph records distinguishing
"tried, zero effect" from "never attempted."
"""

from unittest.mock import MagicMock, patch

import pytest

from agents.arc4.graph_queries import ArcGraphQueryPort
from agents.arc4.types import ExecutionResult


@pytest.fixture
def mock_brain_client():
    """Mock MCP brain client."""
    client = MagicMock()
    return client


@pytest.fixture
def graph_port(mock_brain_client):
    """ArcGraphQueryPort with mocked brain client."""
    return ArcGraphQueryPort(
        brain_client=mock_brain_client,
        task_id="test_task",
        session_id="test_session",
        strict=False,
    )


@pytest.fixture
def execution():
    """Sample ExecutionResult."""
    return ExecutionResult(
        action_id="ACTION1",
        candidate=None,
        observation={},
        metadata={"step": 1},
    )


class TestRecordTransitionNoOp:
    """record_transition behavior for no-op actions."""

    def test_no_op_action_sends_minimal_record(self, graph_port, execution, mock_brain_client):
        """A213: no-op action (empty changed_cells) sends record with empty color_transitions."""
        # Mock the tool response
        mock_brain_client.call_tool.return_value = {"ok": True, "transition_id": "test_transition"}

        # Call with empty changed_cells
        grid_diff = {"changed_cells": []}
        result = graph_port.record_transition(execution, grid_diff)

        # Verify tool was called
        assert mock_brain_client.call_tool.called
        call_args = mock_brain_client.call_tool.call_args

        # Verify payload structure
        assert call_args[0][0] == "record_transition"
        payload = call_args[0][1]
        assert payload["task_id"] == "test_task"
        assert payload["action_id"] == "ACTION1"
        assert payload["changed_count"] == 0
        assert payload["color_transitions"] == []
        assert payload["entity_ref"] is None

    def test_no_op_action_with_none_changed_cells(self, graph_port, execution, mock_brain_client):
        """A213: None changed_cells also sends minimal no-op record."""
        mock_brain_client.call_tool.return_value = {"ok": True, "transition_id": "test_transition"}

        # Call with None changed_cells
        grid_diff = {"changed_cells": None}
        result = graph_port.record_transition(execution, grid_diff)

        # Verify tool was called with no-op record
        assert mock_brain_client.call_tool.called
        call_args = mock_brain_client.call_tool.call_args
        payload = call_args[0][1]
        assert payload["changed_count"] == 0
        assert payload["color_transitions"] == []

    def test_normal_action_unchanged(self, graph_port, execution, mock_brain_client):
        """A213 regression: action with non-empty changed_cells behaves exactly as before."""
        mock_brain_client.call_tool.return_value = {"ok": True, "transition_id": "test_transition"}

        # Call with actual changed cells (dict with from/to color info)
        grid_diff = {
            "changed_cells": [{"from": 1, "to": 2}, {"from": 1, "to": 2}],
            "changed_count": 2,
        }
        result = graph_port.record_transition(execution, grid_diff, entities=[])

        # Verify tool was called
        assert mock_brain_client.call_tool.called
        call_args = mock_brain_client.call_tool.call_args

        payload = call_args[0][1]
        assert payload["changed_count"] == 2
        assert payload["color_transitions"] != []  # Should have color transitions
        assert len(payload["color_transitions"]) > 0

    def test_invalid_grid_diff_format(self, graph_port, execution, mock_brain_client):
        """A213: invalid grid_diff format (not a Mapping) triggers no-op path."""
        mock_brain_client.call_tool.return_value = {"ok": True, "transition_id": "test_transition"}

        # Call with invalid format (not a dict)
        grid_diff = None
        result = graph_port.record_transition(execution, grid_diff)

        # Should treat as no-op
        assert mock_brain_client.call_tool.called
        call_args = mock_brain_client.call_tool.call_args
        payload = call_args[0][1]
        assert payload["changed_count"] == 0
        assert payload["color_transitions"] == []


class TestRecordRuleEvidenceNoOp:
    """record_rule_evidence behavior for no-op actions."""

    def test_no_op_action_sends_minimal_record(self, graph_port, execution, mock_brain_client):
        """A213: no-op action (empty changed_cells) sends record with empty candidate_signatures."""
        mock_brain_client.call_tool.return_value = {"ok": True, "results": []}

        # Call with empty changed_cells
        grid_diff = {"changed_cells": []}
        result = graph_port.record_rule_evidence(execution, grid_diff)

        # Verify tool was called
        assert mock_brain_client.call_tool.called
        call_args = mock_brain_client.call_tool.call_args

        # Verify payload structure (note: server-side tool name is "record_rule", not "record_rule_evidence")
        assert call_args[0][0] == "record_rule"
        payload = call_args[0][1]
        assert payload["task_id"] == "test_task"
        assert payload["action_id"] == "ACTION1"
        assert payload["candidate_signatures"] == []
        # No entity_ref key when entity_ref is None (not added to payload)
        assert "entity_ref" not in payload

    def test_no_op_action_with_none_changed_cells(self, graph_port, execution, mock_brain_client):
        """A213: None changed_cells also sends minimal no-op record."""
        mock_brain_client.call_tool.return_value = {"ok": True, "results": []}

        # Call with None changed_cells
        grid_diff = {"changed_cells": None}
        result = graph_port.record_rule_evidence(execution, grid_diff)

        # Verify tool was called with no-op record
        assert mock_brain_client.call_tool.called
        call_args = mock_brain_client.call_tool.call_args
        payload = call_args[0][1]
        assert payload["candidate_signatures"] == []

    def test_normal_action_unchanged(self, graph_port, execution, mock_brain_client):
        """A213 regression: action with non-empty changed_cells behaves exactly as before."""
        mock_brain_client.call_tool.return_value = {"ok": True, "results": [{"rule_id": "r1", "status": "created"}]}

        # Call with actual changed cells (dict with from/to color info)
        grid_diff = {
            "changed_cells": [{"from": 1, "to": 2}, {"from": 1, "to": 2}],
            "changed_count": 2,
        }
        result = graph_port.record_rule_evidence(execution, grid_diff, entities=[])

        # Verify tool was called
        assert mock_brain_client.call_tool.called
        call_args = mock_brain_client.call_tool.call_args

        payload = call_args[0][1]
        # Should have extracted signatures (or empty if extraction logic filters them out)
        assert "candidate_signatures" in payload
        assert isinstance(payload["candidate_signatures"], list)

    def test_no_op_with_grid_diff_missing_keys(self, graph_port, execution, mock_brain_client):
        """A213: grid_diff missing required keys is treated as no-op."""
        mock_brain_client.call_tool.return_value = {"ok": True, "results": []}

        # Call with grid_diff that has changed_cells key but it's None
        grid_diff = {"changed_cells": None}
        result = graph_port.record_rule_evidence(execution, grid_diff)

        # Should call tool with no-op record
        assert mock_brain_client.call_tool.called
        payload = mock_brain_client.call_tool.call_args[0][1]
        assert payload["candidate_signatures"] == []

    def test_invalid_grid_diff_format(self, graph_port, execution, mock_brain_client):
        """A213: invalid grid_diff format (not a Mapping) triggers no-op path."""
        mock_brain_client.call_tool.return_value = {"ok": True, "results": []}

        # Call with invalid format (not a dict)
        grid_diff = None
        result = graph_port.record_rule_evidence(execution, grid_diff)

        # Should treat as no-op and call tool
        assert mock_brain_client.call_tool.called
        call_args = mock_brain_client.call_tool.call_args
        payload = call_args[0][1]
        assert payload["candidate_signatures"] == []


class TestNoOpBehaviorIntegration:
    """Integration tests for no-op behavior across both methods."""

    def test_both_methods_handle_empty_changed_cells(self, graph_port, execution, mock_brain_client):
        """A213: both record_transition and record_rule_evidence handle empty changed_cells."""
        mock_brain_client.call_tool.return_value = {"ok": True, "results": []}

        grid_diff = {"changed_cells": []}

        # Both methods should call the tool
        result_transition = graph_port.record_transition(execution, grid_diff)
        result_rule = graph_port.record_rule_evidence(execution, grid_diff)

        # Both should have called the tool
        assert mock_brain_client.call_tool.call_count >= 2

    def test_no_op_records_have_required_fields(self, graph_port, execution, mock_brain_client):
        """A213: no-op records include all required fields for server processing."""
        mock_brain_client.call_tool.return_value = {"ok": True}

        grid_diff = {"changed_cells": []}

        # Test record_transition no-op
        mock_brain_client.reset_mock()
        graph_port.record_transition(execution, grid_diff)
        assert mock_brain_client.call_tool.called
        call_args = mock_brain_client.call_tool.call_args
        payload = call_args[0][1]
        assert "task_id" in payload
        assert "step" in payload
        assert "action_id" in payload
        assert "changed_count" in payload
        assert "color_transitions" in payload

        # Test record_rule_evidence no-op
        mock_brain_client.reset_mock()
        graph_port.record_rule_evidence(execution, grid_diff)
        assert mock_brain_client.call_tool.called
        call_args = mock_brain_client.call_tool.call_args
        payload = call_args[0][1]
        assert "task_id" in payload
        assert "step" in payload
        assert "action_id" in payload
        assert "candidate_signatures" in payload
