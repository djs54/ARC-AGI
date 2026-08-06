"""Test A093: Fast prediction falsification and action quarantine.

Tests that repeated prediction misses trigger action quarantine quickly,
preventing continued exploitation of incorrectly predicted actions.
"""

import pytest
from agents.arc3.world_model import WorldModelGraph


class TestPredictionFalsificationTracking:
    """Test tracking of prediction misses."""

    def test_record_falsification_increments_count(self):
        """Recording falsifications increments the count."""
        graph = WorldModelGraph("test_task", "test_session")
        
        graph.record_prediction_falsification(
            action_id="ACTION1",
            predicted_effect="object_progress",
            actual_effect="pixel_churn",
            step=1,
            confidence=0.8
        )
        
        assert graph._action_falsification_count["ACTION1"] == 1

    def test_multiple_falsifications_accumulate(self):
        """Multiple falsifications accumulate correctly."""
        graph = WorldModelGraph("test_task", "test_session")
        
        for i in range(3):
            graph.record_prediction_falsification(
                action_id="ACTION1",
                predicted_effect="object_progress",
                actual_effect="pixel_churn",
                step=i,
                confidence=0.8
            )
        
        assert graph._action_falsification_count["ACTION1"] == 3

    def test_low_confidence_falsifications_dont_quarantine(self):
        """Low-confidence prediction misses don't trigger quarantine."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Record low-confidence falsifications (below 0.7 threshold)
        for i in range(3):
            graph.record_prediction_falsification(
                action_id="ACTION1",
                predicted_effect="object_progress",
                actual_effect="pixel_churn",
                step=i,
                confidence=0.5  # Below threshold
            )
        
        # Action should not be quarantined
        assert not graph.is_action_quarantined("ACTION1", 3)
        assert "ACTION1" not in graph._action_quarantine


class TestActionQuarantine:
    """Test action quarantine state management."""

    def test_quarantine_action_sets_ttl(self):
        """Quarantining an action sets TTL."""
        graph = WorldModelGraph("test_task", "test_session")
        
        graph.quarantine_action("ACTION1", quarantine_until_step=10, reason="test_reason")
        
        assert graph.is_action_quarantined("ACTION1", 5)
        assert graph.is_action_quarantined("ACTION1", 9)

    def test_quarantine_ttl_expires(self):
        """Quarantine TTL expires and action becomes available."""
        graph = WorldModelGraph("test_task", "test_session")
        
        graph.quarantine_action("ACTION1", quarantine_until_step=10, reason="test_reason")
        
        # Before expiry
        assert graph.is_action_quarantined("ACTION1", 5)
        
        # After expiry
        assert not graph.is_action_quarantined("ACTION1", 10)
        assert not graph.is_action_quarantined("ACTION1", 11)

    def test_get_quarantine_state(self):
        """Quarantine state can be retrieved."""
        graph = WorldModelGraph("test_task", "test_session")
        
        graph.quarantine_action("ACTION1", quarantine_until_step=15, reason="predicted_progress_but_churn")
        
        state = graph.get_quarantine_state("ACTION1")
        assert state is not None
        assert state["quarantined_until_step"] == 15
        assert state["reason"] == "predicted_progress_but_churn"

    def test_multiple_actions_quarantine_independently(self):
        """Different actions quarantine independently."""
        graph = WorldModelGraph("test_task", "test_session")
        
        graph.quarantine_action("ACTION1", quarantine_until_step=10, reason="reason1")
        graph.quarantine_action("ACTION2", quarantine_until_step=20, reason="reason2")
        
        assert graph.is_action_quarantined("ACTION1", 5)
        assert graph.is_action_quarantined("ACTION2", 5)
        
        assert not graph.is_action_quarantined("ACTION1", 10)
        assert graph.is_action_quarantined("ACTION2", 10)


class TestHighConfidenceFalsificationQuarantine:
    """Test automatic quarantine on high-confidence falsifications."""

    def test_two_high_confidence_misses_trigger_quarantine(self):
        """Two high-confidence misses (>= 0.7) trigger quarantine."""
        graph = WorldModelGraph("test_task", "test_session")
        
        graph.record_prediction_falsification(
            action_id="ACTION1",
            predicted_effect="object_progress",
            actual_effect="pixel_churn",
            step=1,
            confidence=0.8
        )
        
        graph.record_prediction_falsification(
            action_id="ACTION1",
            predicted_effect="object_progress",
            actual_effect="pixel_churn",
            step=2,
            confidence=0.75
        )
        
        # Should be quarantined now
        assert graph.is_action_quarantined("ACTION1", 3)

    def test_mixed_confidence_levels(self):
        """Mix of high and low confidence predictions triggers quarantine on high."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Low confidence miss
        graph.record_prediction_falsification(
            action_id="ACTION1",
            predicted_effect="object_progress",
            actual_effect="pixel_churn",
            step=1,
            confidence=0.5
        )
        
        # High confidence miss - this is miss #2, and it's high-confidence, so quarantine triggers
        graph.record_prediction_falsification(
            action_id="ACTION1",
            predicted_effect="object_progress",
            actual_effect="pixel_churn",
            step=2,
            confidence=0.8
        )
        
        # Should be quarantined (count >= 2 and current confidence >= 0.7)
        assert graph.is_action_quarantined("ACTION1", 3)

    def test_quarantine_reason_includes_effects(self):
        """Quarantine reason captures predicted vs actual effects."""
        graph = WorldModelGraph("test_task", "test_session")
        
        graph.record_prediction_falsification(
            action_id="ACTION3",
            predicted_effect="object_progress",
            actual_effect="pixel_churn",
            step=5,
            confidence=0.8
        )
        
        graph.record_prediction_falsification(
            action_id="ACTION3",
            predicted_effect="object_progress",
            actual_effect="harmful",
            step=6,
            confidence=0.9
        )
        
        state = graph.get_quarantine_state("ACTION3")
        assert "predicted_" in state["reason"]
        assert "harmful" in state["reason"]


class TestQuarantineIntegration:
    """Test quarantine integration with existing methods."""

    def test_quarantined_action_state_preserved(self):
        """Quarantine state persists across multiple checks."""
        graph = WorldModelGraph("test_task", "test_session")
        
        graph.quarantine_action("ACTION1", 20, "test")
        
        # Multiple checks should return consistent state
        assert graph.is_action_quarantined("ACTION1", 10)
        assert graph.is_action_quarantined("ACTION1", 15)
        assert graph.is_action_quarantined("ACTION1", 19)

    def test_falsification_count_in_quarantine_state(self):
        """Falsification count is preserved in quarantine state."""
        graph = WorldModelGraph("test_task", "test_session")
        
        graph.record_prediction_falsification(
            action_id="ACTION1",
            predicted_effect="object_progress",
            actual_effect="churn",
            step=1,
            confidence=0.8
        )
        
        graph.record_prediction_falsification(
            action_id="ACTION1",
            predicted_effect="object_progress",
            actual_effect="churn",
            step=2,
            confidence=0.8
        )
        
        state = graph.get_quarantine_state("ACTION1")
        assert state["falsification_count"] >= 2

    def test_unquarantined_action_has_no_state(self):
        """Actions that aren't quarantined have no quarantine state."""
        graph = WorldModelGraph("test_task", "test_session")
        
        state = graph.get_quarantine_state("ACTION_NEVER_QUARANTINED")
        assert state is None


class TestQuarantineEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_quarantine_at_step_boundary(self):
        """Quarantine expires exactly at TTL step."""
        graph = WorldModelGraph("test_task", "test_session")
        
        graph.quarantine_action("ACTION1", quarantine_until_step=10, reason="test")
        
        # At step 10 (TTL), should not be quarantined
        assert not graph.is_action_quarantined("ACTION1", 10)

    def test_early_expiry_cleanup(self):
        """Expired quarantine is cleaned up from internal state."""
        graph = WorldModelGraph("test_task", "test_session")
        
        graph.quarantine_action("ACTION1", quarantine_until_step=5, reason="test")
        
        # Before expiry
        assert "ACTION1" in graph._action_quarantine
        
        # After expiry - should clean up
        graph.is_action_quarantined("ACTION1", 5)
        assert "ACTION1" not in graph._action_quarantine

    def test_zero_confidence_falsification(self):
        """Zero or negative confidence doesn't trigger quarantine."""
        graph = WorldModelGraph("test_task", "test_session")
        
        graph.record_prediction_falsification(
            action_id="ACTION1",
            predicted_effect="object_progress",
            actual_effect="churn",
            step=1,
            confidence=0.0
        )
        
        graph.record_prediction_falsification(
            action_id="ACTION1",
            predicted_effect="object_progress",
            actual_effect="churn",
            step=2,
            confidence=0.0
        )
        
        # Should not quarantine
        assert not graph.is_action_quarantined("ACTION1", 2)
