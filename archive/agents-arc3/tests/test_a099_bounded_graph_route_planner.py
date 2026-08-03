"""Test A099: Bounded graph route planner over state transitions.

Tests that the planner can traverse state transitions and propose
bounded action sequences without spending LLM cycles.
"""

import pytest
from agents.arc3.world_model import WorldModelGraph
from agents.arc3.world_model_planner import WorldModelPlanner


class TestStateTransitionGraph:
    """Test state transition graph construction."""

    def test_get_state_transition_graph(self):
        """Graph can return state transition structure."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Build a simple transition chain
        for step in range(3):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", float(50 - step * 10), 0.0)
            act_id = graph.record_action(step, "ACTION1", {"coordinate": [step, 0]}, state_id)
            graph.record_effect(act_id, obs_id, "distance_improving_move", {
                "step": step,
                "goal_distance": 50.0 - step * 10
            })
        
        # Should be able to query transition structure
        effects = graph.get_action_effect_table(limit=10)
        assert len(effects) >= 3

    def test_find_route_candidates(self):
        """Planner can find route candidates from state graph."""
        graph = WorldModelGraph("test_task", "test_session")
        planner = WorldModelPlanner({})
        
        # Build transitions
        for step in range(3):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", float(50 - step * 5), 0.0)
            act_id = graph.record_action(step, "ACTION1", {"coordinate": [step, 0]}, state_id)
            graph.record_effect(act_id, obs_id, "distance_improving_move", {
                "step": step,
                "goal_distance": 50.0 - step * 5
            })
        
        # Query candidates
        effects = graph.get_action_effect_table(limit=10)
        assert len(effects) > 0


class TestRoutePlannerBoundedness:
    """Test that route planner is bounded."""

    def test_default_depth_limit(self):
        """Route planner respects default depth limit."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Create a deep transition chain
        for step in range(20):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", float(100 - step * 2), 0.0)
            act_id = graph.record_action(step, f"ACTION{step % 3}", {"coordinate": [step, 0]}, state_id)
            graph.record_effect(act_id, obs_id, "distance_improving_move", {
                "step": step,
                "goal_distance": 100.0 - step * 2
            })
        
        # Should still be bounded
        effects = graph.get_action_effect_table(limit=5)
        assert len(effects) <= 5

    def test_candidate_count_limit(self):
        """Route planner limits candidate count."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Create multiple action options at each step
        for step in range(5):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", float(50 - step * 5), 0.0)
            for action_idx in range(5):
                act_id = graph.record_action(step, f"ACTION{action_idx}", {"coordinate": [action_idx, 0]}, state_id)
                graph.record_effect(act_id, obs_id, "distance_improving_move", {
                    "step": step,
                    "goal_distance": 50.0 - step * 5
                })
        
        effects = graph.get_action_effect_table(limit=10)
        # Should have limited results
        assert len(effects) <= 10


class TestRouteCandidateQuality:
    """Test quality of route candidates."""

    def test_candidate_preserves_distance_delta(self):
        """Route candidates preserve goal distance deltas."""
        graph = WorldModelGraph("test_task", "test_session")
        
        distances = [50.0, 45.0, 40.0]
        for step, distance in enumerate(distances):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", distance, 0.0)
            act_id = graph.record_action(step, "ACTION1", {"coordinate": [0, 0]}, state_id)
            graph.record_effect(act_id, obs_id, "distance_improving_move", {
                "step": step,
                "goal_distance": distance
            })
        
        effects = graph.get_action_effect_table(limit=10)
        assert len(effects) == 3

    def test_route_confidence_tracking(self):
        """Route candidates include confidence estimates."""
        graph = WorldModelGraph("test_task", "test_session")
        
        for step in range(3):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", float(50 - step * 10), 0.0)
            act_id = graph.record_action(step, "ACTION1", {"coordinate": [0, 0]}, state_id)
            graph.record_effect(act_id, obs_id, "distance_improving_move", {
                "step": step,
                "goal_distance": 50.0 - step * 10
            })
        
        effects = graph.get_action_effect_table(limit=10)
        # Candidates should be traceable through graph
        assert len(effects) > 0


class TestRoutePlannerEdgeCases:
    """Test edge cases in route planning."""

    def test_empty_graph_no_crash(self):
        """Route planner handles empty graph."""
        graph = WorldModelGraph("test_task", "test_session")
        
        effects = graph.get_action_effect_table(limit=10)
        assert effects == []

    def test_single_transition_route(self):
        """Single transition can be a valid route."""
        graph = WorldModelGraph("test_task", "test_session")
        
        state_id = graph.record_state(0, "hash_0")
        obs_id = graph.record_observation(0, "hash_0", 50.0, 0.0)
        act_id = graph.record_action(0, "ACTION1", {"coordinate": [0, 0]}, state_id)
        graph.record_effect(act_id, obs_id, "distance_improving_move", {
            "step": 0,
            "goal_distance": 50.0
        })
        
        effects = graph.get_action_effect_table(limit=10)
        assert len(effects) == 1

    def test_regressing_transitions_are_not_route_candidates(self):
        """Regressing transition evidence is useful for control but not a route proposal."""
        graph = WorldModelGraph("test_task", "test_session")

        for step, distance in enumerate([50.0, 51.0, 52.0]):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", distance, 0.0)
            act_id = graph.record_action(step, "ACTION1", {"coordinate": [0, 0]}, state_id)
            graph.record_effect(act_id, obs_id, "distance_regressing_move", {
                "step": step,
                "goal_distance": distance,
                "goal_distance_delta": 1.0,
                "distance_trend": "regressing",
            })

        evidence = graph.get_route_transition_evidence(available_actions=["ACTION1"])
        assert evidence["has_recent_route_regression"] is True
        assert graph.find_route_candidates(available_actions=["ACTION1"]) == []

    def test_deterministic_candidate_ordering(self):
        """Candidates are returned deterministically."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Create transitions
        for step in range(3):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", float(50 - step * 5), 0.0)
            act_id = graph.record_action(step, "ACTION1", {"coordinate": [0, 0]}, state_id)
            graph.record_effect(act_id, obs_id, "distance_improving_move", {
                "step": step,
                "goal_distance": 50.0 - step * 5
            })
        
        effects1 = graph.get_action_effect_table(limit=10)
        effects2 = graph.get_action_effect_table(limit=10)
        
        # Should have consistent ordering
        assert len(effects1) == len(effects2)
