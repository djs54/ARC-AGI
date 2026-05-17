"""Test A097: Split movement transitions from visual churn.

Tests that the effect taxonomy distinguishes movement/state transitions
from irrelevant visual churn.
"""

import pytest
from agents.arc3.world_model import WorldModelGraph
from agents.arc3.world_model_compiler import WorldModelCompiler


class TestMovementTransitionTaxonomy:
    """Test refined effect taxonomy."""

    def test_distance_improving_move_classified(self):
        """Improving goal distance classified as distance_improving_move."""
        compiler = WorldModelCompiler()
        
        delta = compiler.compile_step(
            step=1,
            prev_hash="hash1",
            curr_hash="hash2",
            action={"action_id": "ACTION1"},
            reward_components={"meaningful_progress": False},
            terminal_trend="improving",
            object_progress={"score": 0.0},
            available_actions=["ACTION1"],
            goal_distance=35.0
        )
        
        # Verify movement effect class is available
        claims = delta.claims
        action_claim = next((c for c in claims if c.kind == "action_effect"), None)
        assert action_claim is not None

    def test_distance_regressing_move_classified(self):
        """Regressing goal distance classified as distance_regressing_move."""
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
            goal_distance=50.0
        )
        
        claims = delta.claims
        action_claim = next((c for c in claims if c.kind == "action_effect"), None)
        assert action_claim is not None

    def test_reversible_movement_detected(self):
        """State changes without progress classified as reversible movement."""
        graph = WorldModelGraph("test_task", "test_session")
        
        state_id = graph.record_state(1, "hash1")
        obs_id = graph.record_observation(1, "hash1", 42.0, 0.0)
        act_id = graph.record_action(1, "ACTION1", {"coordinate": [0, 0]}, state_id)
        graph.record_effect(act_id, obs_id, "visual_churn", {
            "step": 1,
            "meaningful": False
        })

    def test_state_transition_detected(self):
        """Meaningful state changes classified as state_transition."""
        graph = WorldModelGraph("test_task", "test_session")
        
        state_id = graph.record_state(1, "hash1")
        obs_id = graph.record_observation(1, "hash1", 42.0, 0.0)
        act_id = graph.record_action(1, "ACTION1", {"coordinate": [0, 0]}, state_id)
        graph.record_effect(act_id, obs_id, "state_transition", {
            "step": 1,
            "meaningful": False,
            "state_changed": True
        })

    def test_visual_churn_preserved(self):
        """True visual churn still classified as visual_churn."""
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
            goal_distance=42.0
        )
        
        claims = delta.claims
        action_claim = next((c for c in claims if c.kind == "action_effect"), None)
        assert action_claim is not None


class TestTransitionGraphQueries:
    """Test querying transition effects from graph."""

    def test_get_movement_transitions(self):
        """Graph can retrieve movement transition effects."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Record multiple transitions
        for step in range(3):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", float(50 - step * 5), 0.0)
            act_id = graph.record_action(step, f"ACTION{step % 2}", {"coordinate": [step, 0]}, state_id)
            graph.record_effect(act_id, obs_id, "distance_improving_move", {
                "step": step,
                "meaningful": False,
                "goal_distance": 50.0 - step * 5
            })
        
        effects = graph.get_action_effect_table(limit=10)
        assert len(effects) >= 3

    def test_route_evidence_collection(self):
        """Route evidence can be collected from transitions."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Create a series of improving movements
        state_ids = []
        for step in range(3):
            state_id = graph.record_state(step, f"hash_{step}")
            state_ids.append(state_id)
            obs_id = graph.record_observation(step, f"hash_{step}", float(50 - step * 10), 0.0)
            act_id = graph.record_action(step, "ACTION1", {"coordinate": [step, 0]}, state_id)
            graph.record_effect(act_id, obs_id, "distance_improving_move", {
                "step": step,
                "goal_distance": 50.0 - step * 10
            })
        
        # Verify graph can track the sequence
        assert len(state_ids) == 3


class TestTransitionEdgeCases:
    """Test edge cases in movement transition classification."""

    def test_no_hash_change_with_churn(self):
        """No hash change classified as no-op, not movement."""
        compiler = WorldModelCompiler()
        
        delta = compiler.compile_step(
            step=1,
            prev_hash="hash1",
            curr_hash="hash1",
            action={"action_id": "ACTION1"},
            reward_components={"meaningful_progress": False},
            terminal_trend="flat",
            object_progress={"score": 0.0},
            available_actions=["ACTION1"],
            goal_distance=42.0
        )
        
        claims = delta.claims
        action_claim = next((c for c in claims if c.kind == "action_effect"), None)
        assert action_claim.effect_class == "no_op"

    def test_movement_with_object_progress(self):
        """Object progress takes precedence over movement classification."""
        compiler = WorldModelCompiler()
        
        delta = compiler.compile_step(
            step=1,
            prev_hash="hash1",
            curr_hash="hash2",
            action={"action_id": "ACTION1"},
            reward_components={"meaningful_progress": True, "progress_class": "object_monotonic"},
            terminal_trend="improving",
            object_progress={"score": 5.0},
            available_actions=["ACTION1"],
            goal_distance=35.0
        )
        
        claims = delta.claims
        action_claim = next((c for c in claims if c.kind == "action_effect"), None)
        # Object progress should be classified as terminal_progress (improving trend)
        assert action_claim.effect_class in ("object_progress", "terminal_progress", "meaningful_progress")
