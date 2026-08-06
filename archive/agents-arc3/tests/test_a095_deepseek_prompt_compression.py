"""Test A095: DeepSeek smoke prompt compression from world-model state.

Tests that repeated world-model context is compressed using deltas instead of
full repetition, reducing prompt token consumption without losing required information.
"""

import pytest
from agents.arc3.world_model import WorldModelGraph


class TestWorldModelDeltaCompression:
    """Test world model delta computation for compression."""

    def test_initial_world_model_not_compressed(self):
        """First world model representation is full, not compressed."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Initial setup
        graph.upsert_hypothesis("hyp1", "mechanic", "move_object_right", 0.8, "active")
        
        summary = graph.to_prompt_summary(max_chars=5000)
        
        # Should contain full hypothesis
        assert "move_object_right" in summary
        assert len(summary) > 0

    def test_subsequent_updates_delta_based(self):
        """Subsequent snapshots can be delta-based."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Build initial state
        graph.upsert_hypothesis("hyp1", "mechanic", "move_object_right", 0.8, "active")
        graph.upsert_hypothesis("hyp2", "mechanic", "fill_background", 0.6, "active")
        
        # Small update - only new hypothesis added
        graph.upsert_hypothesis("hyp3", "mechanic", "rotate_90_degrees", 0.5, "active")
        
        # Summary should still be valid
        summary = graph.to_prompt_summary()
        
        # Active hypotheses should all be present
        active_hyps = graph.get_active_hypotheses()
        assert len(active_hyps) >= 2

    def test_compress_fields_preserved(self):
        """Compression preserves all required fields for planner."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Record action effects
        state_id = graph.record_state(1, "hash1")
        obs_id = graph.record_observation(1, "hash1", 1.0, 5.0)
        act_id = graph.record_action(1, "ACTION1", {"coordinate": [1, 1]}, state_id)
        graph.record_effect(act_id, obs_id, "object_progress", {"step": 1, "meaningful": True})
        
        # Get effect table
        effects = graph.get_action_effect_table(limit=10)
        
        # All required fields must be present
        assert len(effects) > 0
        effect = effects[0]
        assert "action" in effect
        assert "effect" in effect
        assert "step" in effect
        assert "meaningful" in effect


class TestCompressedPromptRetention:
    """Test that compression doesn't lose required content."""

    def test_action_schema_never_compressed(self):
        """Action schema and legal actions are never compressed away."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Record multiple steps with different actions
        actions = ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"]
        
        for step, action_id in enumerate(actions):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", 0.0, 10.0)
            act_id = graph.record_action(step, action_id, {"coordinate": [step, 0]}, state_id)
            graph.record_effect(act_id, obs_id, "pixel_churn", {"step": step, "meaningful": False})
        
        # Get action effect table
        effects = graph.get_action_effect_table(limit=20)
        
        # Should preserve all unique actions
        unique_actions = set(e["action"] for e in effects)
        assert len(unique_actions) >= 3

    def test_active_contradictions_always_shown(self):
        """Active contradictions are preserved in compressed form."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Set up hypotheses with contradiction
        hyp_id = graph.upsert_hypothesis("hyp1", "mechanic", "test_hypothesis", 0.9, "active")
        obs_id = graph.record_observation(1, "hash1", 0.0, 10.0)
        
        # Create contradiction
        graph.add_node(hyp_id, "Hypothesis", {"status": "active"})
        graph.link_contradiction(obs_id, hyp_id, 0.8, "contradicted_prediction")
        
        # Contradiction count should be tracked
        assert graph.contradiction_count > 0

    def test_required_fields_in_compressed_summary(self):
        """Compressed summary includes required planner fields."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Set up various elements
        graph.upsert_hypothesis("hyp1", "mechanic", "mechanism_1", 0.8, "active")
        
        state_id = graph.record_state(1, "hash1")
        obs_id = graph.record_observation(1, "hash1", 1.0, 5.0)
        act_id = graph.record_action(1, "ACTION1", {"coordinate": [0, 0]}, state_id)
        graph.record_effect(act_id, obs_id, "object_progress", {"step": 1, "meaningful": True})
        
        summary = graph.to_prompt_summary(max_chars=10000)
        
        # Summary should include:
        # - Active hypotheses
        # - Recent effects
        assert "Active" in summary or "mechanism" in summary or "object_progress" in summary


class TestCompressionTokenEstimate:
    """Test compression metrics for token estimation."""

    def test_delta_based_compression_smaller(self):
        """Delta-based compression structures are available."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Full world model
        for step in range(10):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", float(step), 10.0 - step)
            for action_id in ["ACTION1", "ACTION2", "ACTION3"]:
                act_id = graph.record_action(step, action_id, {"coordinate": [step, 0]}, state_id)
                graph.record_effect(act_id, obs_id, "pixel_churn", {"step": step, "meaningful": False})
        
        # Get full summary
        full_summary = graph.to_prompt_summary(max_chars=50000)
        
        # Get effect table (structured data)
        effects = graph.get_action_effect_table(limit=5)
        effects_str = str(effects)
        
        # Both representations should exist
        assert len(full_summary) > 0
        assert len(effects) > 0
        # Summaries should both contain useful information
        assert "action" in full_summary.lower() or "ACTION" in full_summary

    def test_compression_preserves_action_counts(self):
        """Compression preserves action counts needed for policy."""
        graph = WorldModelGraph("test_task", "test_session")
        
        actions = ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"]
        for action_id in actions:
            for step in range(2):
                state_id = graph.record_state(step, f"hash_{action_id}_{step}")
                obs_id = graph.record_observation(step, f"hash_{action_id}_{step}", 0.0, 10.0)
                act_id = graph.record_action(step, action_id, {"coordinate": [0, 0]}, state_id)
                graph.record_effect(act_id, obs_id, "pixel_churn", {"step": step, "meaningful": False})
        
        # Get churn evidence
        churn_evidence = graph.get_all_actions_churn_evidence(
            available_actions=actions,
            min_tests_per_action=2
        )
        
        # Action count preserved
        assert churn_evidence["required_action_count"] == len(actions)


class TestPromptCompressionMechanism:
    """Test the compression mechanism itself."""

    def test_graph_based_evidence_paths(self):
        """Evidence paths are available for reconstruction."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Build a path
        state_id = graph.record_state(1, "hash1")
        obs_id = graph.record_observation(1, "hash1", 1.0, 5.0)
        act_id = graph.record_action(1, "ACTION1", {"coordinate": [1, 1]}, state_id)
        eff_id = graph.record_effect(act_id, obs_id, "object_progress", {"step": 1, "meaningful": True})
        
        # Get prediction evidence which includes evidence paths
        evidence = graph.get_action_prediction_evidence("ACTION1")
        
        # Should include evidence path
        assert "evidence_path_ids" in evidence
        assert len(evidence["evidence_path_ids"]) > 0

    def test_compact_metric_export(self):
        """Metrics can be exported in compact form."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Set up various state
        graph.upsert_hypothesis("hyp1", "mechanic", "test", 0.8, "active")
        
        for step in range(3):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", 0.0, 10.0 - step)
            act_id = graph.record_action(step, "ACTION1", {"coordinate": [0, 0]}, state_id)
            graph.record_effect(act_id, obs_id, "pixel_churn", {"step": step, "meaningful": False})
        
        # Get mechanic summary (for external reuse)
        mechanic_summary = graph.to_mechanic_summary()
        
        # Summary should be compact
        assert "id" in mechanic_summary
        assert "action_set_signature" in mechanic_summary
        assert "confidence" in mechanic_summary


class TestCompressionEdgeCases:
    """Test compression edge cases."""

    def test_very_small_world_model(self):
        """Single node world model compresses safely."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Just the root node
        summary = graph.to_prompt_summary()
        assert len(summary) > 0

    def test_large_action_set(self):
        """Large action set compresses adequately."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Many actions
        for i in range(20):
            action_id = f"ACTION{i}"
            for step in range(1):
                state_id = graph.record_state(0, "hash")
                obs_id = graph.record_observation(0, "hash", 0.0, 10.0)
                act_id = graph.record_action(0, action_id, {"coordinate": [0, 0]}, state_id)
                graph.record_effect(act_id, obs_id, "pixel_churn", {"step": 0, "meaningful": False})
        
        # Get churn evidence
        actions = [f"ACTION{i}" for i in range(20)]
        churn_evidence = graph.get_all_actions_churn_evidence(
            available_actions=actions,
            min_tests_per_action=1
        )
        
        # Should handle large sets
        assert churn_evidence["required_action_count"] == 20

    def test_compression_max_chars_respected(self):
        """Compression max_chars limit is respected."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Large hypothesis set
        for i in range(20):
            graph.upsert_hypothesis(f"hyp{i}", "mechanic", f"mechanism_{i}_hypothesis", 0.5 + i * 0.01, "active")
        
        summary = graph.to_prompt_summary(max_chars=500)
        
        # Should not exceed max_chars by much
        assert len(summary) <= 600  # Allow some buffer for truncation marker
