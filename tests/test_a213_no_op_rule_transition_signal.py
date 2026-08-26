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


class TestRecordRuleEvidenceStillSkipsNoOp:
    """record_rule_evidence behavior for no-op actions -- investigated for a
    no-op-signal branch mirroring record_transition's, but reverted on
    review: hippocampy's server-side record_rule loops over
    candidate_signatures and writes nothing when the list is empty (no node,
    no edge), unlike record_transition's unconditional Transition-node
    MERGE. Sending an empty-signatures call would be a wasted round-trip,
    not real signal, so this path deliberately keeps its original
    skip-the-call behavior. See backlog/A213.md's Outcome."""

    def test_no_op_action_skips_the_call(self, graph_port, execution, mock_brain_client):
        grid_diff = {"changed_cells": []}
        result = graph_port.record_rule_evidence(execution, grid_diff)

        assert result == {"status": "no_changes", "recorded": False}
        assert not mock_brain_client.call_tool.called

    def test_none_changed_cells_skips_the_call(self, graph_port, execution, mock_brain_client):
        grid_diff = {"changed_cells": None}
        result = graph_port.record_rule_evidence(execution, grid_diff)

        assert result == {"status": "no_changes", "recorded": False}
        assert not mock_brain_client.call_tool.called

    def test_normal_action_unchanged(self, graph_port, execution, mock_brain_client):
        """Regression: action with non-empty changed_cells still calls through as before this card."""
        mock_brain_client.call_tool.return_value = {"ok": True, "results": [{"rule_id": "r1", "status": "created"}]}

        grid_diff = {
            "changed_cells": [{"from": 1, "to": 2}, {"from": 1, "to": 2}],
            "changed_count": 2,
        }
        result = graph_port.record_rule_evidence(execution, grid_diff, entities=[])

        assert mock_brain_client.call_tool.called
        call_args = mock_brain_client.call_tool.call_args
        payload = call_args[0][1]
        assert "candidate_signatures" in payload
        assert isinstance(payload["candidate_signatures"], list)

    def test_invalid_grid_diff_format_skips_the_call(self, graph_port, execution, mock_brain_client):
        grid_diff = None
        result = graph_port.record_rule_evidence(execution, grid_diff)

        assert result == {"status": "no_changes", "recorded": False}
        assert not mock_brain_client.call_tool.called


class TestNoOpBehaviorIntegration:
    """Integration tests spanning both methods -- they deliberately diverge:
    record_transition sends a no-op record (effective, per its class above);
    record_rule_evidence still skips the call entirely (ineffective if sent,
    per the class above)."""

    def test_transition_calls_while_rule_evidence_skips(self, graph_port, execution, mock_brain_client):
        mock_brain_client.call_tool.return_value = {"ok": True, "transition_id": "t1"}
        grid_diff = {"changed_cells": []}

        result_transition = graph_port.record_transition(execution, grid_diff)
        assert mock_brain_client.call_tool.call_count == 1

        mock_brain_client.reset_mock()
        result_rule = graph_port.record_rule_evidence(execution, grid_diff)
        assert mock_brain_client.call_tool.call_count == 0
        assert result_rule == {"status": "no_changes", "recorded": False}

    def test_no_op_transition_record_has_required_fields(self, graph_port, execution, mock_brain_client):
        mock_brain_client.call_tool.return_value = {"ok": True}
        grid_diff = {"changed_cells": []}

        graph_port.record_transition(execution, grid_diff)
        assert mock_brain_client.call_tool.called
        payload = mock_brain_client.call_tool.call_args[0][1]
        assert "task_id" in payload
        assert "step" in payload
        assert "action_id" in payload
        assert "changed_count" in payload
        assert "color_transitions" in payload
