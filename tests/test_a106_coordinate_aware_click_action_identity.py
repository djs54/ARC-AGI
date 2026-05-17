"""Test A106 — Coordinate-aware click action identity."""

import pytest
from agents.arc3.world_model import WorldModelGraph, build_action_identity, COORDINATE_REQUIRED_ACTIONS


class TestBuildActionIdentity:
    """Test the build_action_identity helper function."""

    def test_action6_with_coordinates(self):
        """ACTION6 with x,y should return ACTION6@x,y."""
        result = build_action_identity("ACTION6", x=10, y=12)
        assert result == "ACTION6@10,12"

    def test_action6_with_floats(self):
        """ACTION6 with float coordinates should convert to int."""
        result = build_action_identity("ACTION6", x=10.5, y=12.7)
        assert result == "ACTION6@10,12"

    def test_action6_without_coordinates(self):
        """ACTION6 without x,y should return ACTION6."""
        result = build_action_identity("ACTION6")
        assert result == "ACTION6"

    def test_action6_with_none_x(self):
        """ACTION6 with x=None should return ACTION6."""
        result = build_action_identity("ACTION6", x=None, y=12)
        assert result == "ACTION6"

    def test_action6_with_none_y(self):
        """ACTION6 with y=None should return ACTION6."""
        result = build_action_identity("ACTION6", x=10, y=None)
        assert result == "ACTION6"

    def test_non_coordinate_action(self):
        """Non-coordinate actions should ignore x,y."""
        result = build_action_identity("ACTION0", x=10, y=12)
        assert result == "ACTION0"

    def test_action1_ignores_coordinates(self):
        """ACTION1 (not in COORDINATE_REQUIRED_ACTIONS) should ignore x,y."""
        result = build_action_identity("ACTION1", x=10, y=12)
        assert result == "ACTION1"


class TestWorldModelActionIdentity:
    """Test action identity storage in world model."""

    def test_record_action_with_coordinates(self):
        """record_action should store action_identity with coordinates."""
        graph = WorldModelGraph("test_task", "test_session")
        state_id = graph.record_state(0, "frame_hash_1")
        
        action_id = graph.record_action(
            step=1,
            action_id="ACTION6",
            args={"x": 10, "y": 12},
            state_id=state_id
        )
        
        action_node = graph.nodes[action_id]
        assert action_node.props["action_id"] == "ACTION6"
        assert action_node.props["action_identity"] == "ACTION6@10,12"
        assert action_node.props["coordinate_required"] is True
        assert action_node.props["missing_coordinate_click"] is False

    def test_record_action_without_coordinates(self):
        """record_action with missing coordinates should be marked."""
        graph = WorldModelGraph("test_task", "test_session")
        state_id = graph.record_state(0, "frame_hash_1")
        
        action_id = graph.record_action(
            step=1,
            action_id="ACTION6",
            args={},
            state_id=state_id
        )
        
        action_node = graph.nodes[action_id]
        assert action_node.props["action_id"] == "ACTION6"
        assert action_node.props["action_identity"] == "ACTION6"
        assert action_node.props["coordinate_required"] is True
        assert action_node.props["missing_coordinate_click"] is True

    def test_record_action_non_coordinate(self):
        """record_action for non-coordinate actions should not mark as coordinate_required."""
        graph = WorldModelGraph("test_task", "test_session")
        state_id = graph.record_state(0, "frame_hash_1")
        
        action_id = graph.record_action(
            step=1,
            action_id="ACTION0",
            args={},
            state_id=state_id
        )
        
        action_node = graph.nodes[action_id]
        assert action_node.props["action_id"] == "ACTION0"
        assert action_node.props["action_identity"] == "ACTION0"
        assert action_node.props["coordinate_required"] is False
        assert action_node.props["missing_coordinate_click"] is False


class TestActionIdentityQuarantine:
    """Test coordinate-aware action identity quarantine."""

    def test_quarantine_action_identity(self):
        """quarantine_action_identity should mark action_identity as quarantined."""
        graph = WorldModelGraph("test_task", "test_session")
        
        graph.quarantine_action_identity("ACTION6@10,12", quarantine_until_step=10, reason="test")
        
        assert graph.is_action_identity_quarantined("ACTION6@10,12", current_step=5) is True
        assert graph.is_action_identity_quarantined("ACTION6@10,12", current_step=10) is False

    def test_different_coordinates_not_quarantined(self):
        """Quarantining ACTION6@10,12 should not quarantine ACTION6@22,8."""
        graph = WorldModelGraph("test_task", "test_session")
        
        graph.quarantine_action_identity("ACTION6@10,12", quarantine_until_step=10, reason="test")
        
        assert graph.is_action_identity_quarantined("ACTION6@10,12", current_step=5) is True
        assert graph.is_action_identity_quarantined("ACTION6@22,8", current_step=5) is False

    def test_record_action_identity_falsification(self):
        """record_action_identity_falsification should trigger quarantine at count=2 with high confidence."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # First falsification should not quarantine
        graph.record_action_identity_falsification("ACTION6@10,12", "config_change", "no_change", step=1, confidence=0.8)
        assert graph.is_action_identity_quarantined("ACTION6@10,12", current_step=2) is False
        
        # Second falsification with high confidence should quarantine
        graph.record_action_identity_falsification("ACTION6@10,12", "config_change", "no_change", step=2, confidence=0.8)
        assert graph.is_action_identity_quarantined("ACTION6@10,12", current_step=3) is True

    def test_low_confidence_falsification_no_quarantine(self):
        """record_action_identity_falsification with low confidence should not quarantine."""
        graph = WorldModelGraph("test_task", "test_session")
        
        graph.record_action_identity_falsification("ACTION6@10,12", "config_change", "no_change", step=1, confidence=0.5)
        graph.record_action_identity_falsification("ACTION6@10,12", "config_change", "no_change", step=2, confidence=0.5)
        
        assert graph.is_action_identity_quarantined("ACTION6@10,12", current_step=3) is False

    def test_get_action_identity_quarantine_state(self):
        """get_action_identity_quarantine_state should return quarantine metadata."""
        graph = WorldModelGraph("test_task", "test_session")
        
        graph.quarantine_action_identity("ACTION6@10,12", quarantine_until_step=10, reason="test_reason")
        state = graph.get_action_identity_quarantine_state("ACTION6@10,12")
        
        assert state is not None
        assert state["quarantined_until_step"] == 10
        assert state["reason"] == "test_reason"

    def test_quarantine_ttl_expiration(self):
        """Quarantine should expire at quarantined_until_step."""
        graph = WorldModelGraph("test_task", "test_session")
        
        graph.quarantine_action_identity("ACTION6@10,12", quarantine_until_step=5, reason="test")
        
        # Before TTL expiration
        assert graph.is_action_identity_quarantined("ACTION6@10,12", current_step=4) is True
        
        # At TTL expiration
        assert graph.is_action_identity_quarantined("ACTION6@10,12", current_step=5) is False
        
        # After TTL expiration, should return False and remove from quarantine dict
        assert "ACTION6@10,12" not in graph._action_identity_quarantine


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
