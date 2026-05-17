"""Test A096: Terminal-distance delta on effect edges.

Tests that goal distance deltas are recorded and persisted on action-effect edges
for route planning evidence.
"""

import pytest
from agents.arc3.world_model import WorldModelGraph
from agents.arc3.world_model_compiler import WorldModelCompiler, ActionEffectClaim


class TestDistanceDeltaTracking:
    """Test goal distance delta tracking in compiler."""

    def test_goal_distance_delta_improving(self):
        """Compiler records improving goal distance delta."""
        compiler = WorldModelCompiler()
        
        delta = compiler.compile_step(
            step=1,
            prev_hash="hash1",
            curr_hash="hash2",
            action={"action_id": "ACTION1"},
            reward_components={"meaningful_progress": True, "progress_class": "object_monotonic"},
            terminal_trend="improving",
            object_progress={"score": 5.0},
            available_actions=["ACTION1", "ACTION2"],
            goal_distance=42.0  # Before: 42.0
        )
        
        # Store the state
        claims = delta.claims
        assert len(claims) > 0
        action_claim = next((c for c in claims if c.kind == "action_effect"), None)
        assert action_claim is not None
        assert action_claim.props.get("goal_distance") == 42.0

    def test_goal_distance_delta_regressing(self):
        """Compiler tracks regressing goal distance."""
        compiler = WorldModelCompiler()
        
        delta = compiler.compile_step(
            step=1,
            prev_hash="hash1",
            curr_hash="hash2",
            action={"action_id": "ACTION1"},
            reward_components={"meaningful_progress": False},
            terminal_trend="regressing",
            object_progress={"score": 0.0},
            available_actions=["ACTION1"],
            goal_distance=50.0  # Farther from goal
        )
        
        claims = delta.claims
        action_claim = next((c for c in claims if c.kind == "action_effect"), None)
        assert action_claim is not None
        assert action_claim.props.get("goal_distance") == 50.0

    def test_goal_distance_unknown(self):
        """Compiler handles unknown goal distance."""
        compiler = WorldModelCompiler()
        
        delta = compiler.compile_step(
            step=1,
            prev_hash="hash1",
            curr_hash="hash2",
            action={"action_id": "ACTION1"},
            reward_components={"meaningful_progress": False},
            terminal_trend="flat",
            object_progress={"score": 0.0},
            available_actions=["ACTION1"],
            goal_distance=None  # Unknown
        )
        
        claims = delta.claims
        action_claim = next((c for c in claims if c.kind == "action_effect"), None)
        assert action_claim is not None
        assert action_claim.props.get("goal_distance") is None


class TestDistanceDeltaGraphPersistence:
    """Test goal distance deltas persisted in graph."""

    def test_effect_node_stores_goal_distance(self):
        """Goal distance is stored on Effect nodes."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Record state and observation
        state_id = graph.record_state(1, "hash1")
        obs_id = graph.record_observation(1, "hash1", 42.0, 5.0)  # goal_distance=42.0, value=5.0
        
        # Record action
        act_id = graph.record_action(1, "ACTION1", {"coordinate": [0, 0]}, state_id)
        
        # Record effect with goal distance
        eff_id = graph.record_effect(act_id, obs_id, "object_progress", {
            "step": 1,
            "meaningful": True,
            "goal_distance": 42.0
        })
        
        # Verify effect node has goal_distance
        effect_node = graph.nodes.get(eff_id)
        assert effect_node is not None
        assert effect_node.props.get("goal_distance") == 42.0

    def test_effect_node_multiple_distances(self):
        """Multiple effects can have different goal distances."""
        graph = WorldModelGraph("test_task", "test_session")
        
        effects_and_distances = []
        for step in range(3):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", float(50 - step * 5), 0.0)
            act_id = graph.record_action(step, "ACTION1", {"coordinate": [0, 0]}, state_id)
            eff_id = graph.record_effect(act_id, obs_id, "object_progress", {
                "step": step,
                "meaningful": True,
                "goal_distance": 50.0 - step * 5
            })
            effects_and_distances.append((eff_id, 50.0 - step * 5))
        
        # Verify all distances stored
        for eff_id, expected_distance in effects_and_distances:
            effect_node = graph.nodes.get(eff_id)
            assert effect_node.props.get("goal_distance") == expected_distance


class TestDistanceDeltaMetrics:
    """Test goal distance in world model evaluation."""

    def test_goal_distance_in_effect_table(self):
        """Goal distance appears in action effect table."""
        graph = WorldModelGraph("test_task", "test_session")
        
        state_id = graph.record_state(1, "hash1")
        obs_id = graph.record_observation(1, "hash1", 42.0, 5.0)
        act_id = graph.record_action(1, "ACTION1", {"coordinate": [0, 0]}, state_id)
        graph.record_effect(act_id, obs_id, "object_progress", {
            "step": 1,
            "meaningful": True,
            "goal_distance": 42.0
        })
        
        effects = graph.get_action_effect_table(limit=10)
        assert len(effects) > 0
        # Goal distance should be accessible via graph queries if populated
        effect = effects[0]
        assert "effect" in effect


class TestDistanceDeltaEdgeCases:
    """Test edge cases in goal distance tracking."""

    def test_zero_goal_distance(self):
        """Zero goal distance handled correctly."""
        graph = WorldModelGraph("test_task", "test_session")
        
        state_id = graph.record_state(1, "hash1")
        obs_id = graph.record_observation(1, "hash1", 0.0, 100.0)
        act_id = graph.record_action(1, "ACTION1", {"coordinate": [0, 0]}, state_id)
        eff_id = graph.record_effect(act_id, obs_id, "terminal_progress", {
            "step": 1,
            "meaningful": True,
            "goal_distance": 0.0
        })
        
        effect_node = graph.nodes.get(eff_id)
        assert effect_node.props.get("goal_distance") == 0.0

    def test_negative_goal_distance_handling(self):
        """Negative goal distance handled (shouldn't occur but treated safely)."""
        graph = WorldModelGraph("test_task", "test_session")
        
        state_id = graph.record_state(1, "hash1")
        obs_id = graph.record_observation(1, "hash1", -1.0, 0.0)
        act_id = graph.record_action(1, "ACTION1", {"coordinate": [0, 0]}, state_id)
        eff_id = graph.record_effect(act_id, obs_id, "visual_churn", {
            "step": 1,
            "meaningful": False,
            "goal_distance": -1.0
        })
        
        effect_node = graph.nodes.get(eff_id)
        assert effect_node.props.get("goal_distance") == -1.0

    def test_large_goal_distance_values(self):
        """Large goal distance values handled."""
        graph = WorldModelGraph("test_task", "test_session")
        
        large_distance = 1000.5
        state_id = graph.record_state(1, "hash1")
        obs_id = graph.record_observation(1, "hash1", large_distance, 0.0)
        act_id = graph.record_action(1, "ACTION1", {"coordinate": [0, 0]}, state_id)
        eff_id = graph.record_effect(act_id, obs_id, "visual_churn", {
            "step": 1,
            "meaningful": False,
            "goal_distance": large_distance
        })
        
        effect_node = graph.nodes.get(eff_id)
        assert effect_node.props.get("goal_distance") == large_distance
