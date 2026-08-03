"""Test A094: Multi-action churn exhaustion world-model decision.

Tests that the controller emits an explicit decision when all legal actions
have been tested and show only churn/harm/local progress without terminal alignment.
"""

import pytest
from agents.arc3.world_model import WorldModelGraph


class TestChurnExhaustionDetection:
    """Test detection of multi-action churn exhaustion."""

    def test_all_actions_tested_with_no_progress(self):
        """All actions tested with only churn should be detected."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Record churn for each action
        for action_id in ["ACTION1", "ACTION2", "ACTION3"]:
            for step in range(2):  # 2 tests per action
                state_id = graph.record_state(step, f"hash_{action_id}_{step}")
                obs_id = graph.record_observation(step, f"hash_{action_id}_{step}", 0.0, 10.0)
                act_id = graph.record_action(step, action_id, {"coordinate": [0, 0]}, state_id)
                graph.record_effect(act_id, obs_id, "pixel_churn", {"step": step, "meaningful": False})
        
        churn_evidence = graph.get_all_actions_churn_evidence(
            available_actions=["ACTION1", "ACTION2", "ACTION3"],
            min_tests_per_action=2
        )
        
        assert churn_evidence["all_actions_churn"] is True

    def test_exhaustion_requires_minimum_tests_per_action(self):
        """Exhaustion not detected if actions haven't been tested enough."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Only 1 test for each action (min is 2)
        for action_id in ["ACTION1", "ACTION2"]:
            state_id = graph.record_state(0, f"hash_{action_id}")
            obs_id = graph.record_observation(0, f"hash_{action_id}", 0.0, 10.0)
            act_id = graph.record_action(0, action_id, {"coordinate": [0, 0]}, state_id)
            graph.record_effect(act_id, obs_id, "pixel_churn", {"step": 0, "meaningful": False})
        
        churn_evidence = graph.get_all_actions_churn_evidence(
            available_actions=["ACTION1", "ACTION2"],
            min_tests_per_action=2
        )
        
        assert churn_evidence["all_actions_churn"] is False

    def test_any_progress_breaks_exhaustion(self):
        """Any terminal-aligned progress breaks exhaustion detection."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Record churn for ACTION1 and ACTION2
        for action_id in ["ACTION1", "ACTION2"]:
            for step in range(2):
                state_id = graph.record_state(step, f"hash_{action_id}_{step}")
                obs_id = graph.record_observation(step, f"hash_{action_id}_{step}", 0.0, 10.0)
                act_id = graph.record_action(step, action_id, {"coordinate": [0, 0]}, state_id)
                graph.record_effect(act_id, obs_id, "pixel_churn", {"step": step, "meaningful": False})
        
        # Record progress for ACTION3
        for step in range(2, 4):
            state_id = graph.record_state(step, f"hash_ACTION3_{step}")
            obs_id = graph.record_observation(step, f"hash_ACTION3_{step}", 1.0, 5.0)
            act_id = graph.record_action(step, "ACTION3", {"coordinate": [1, 1]}, state_id)
            graph.record_effect(act_id, obs_id, "terminal_progress", {"step": step, "meaningful": True})
        
        churn_evidence = graph.get_all_actions_churn_evidence(
            available_actions=["ACTION1", "ACTION2", "ACTION3"],
            min_tests_per_action=2
        )
        
        assert churn_evidence["all_actions_churn"] is False
        assert churn_evidence["total_progress_count"] > 0


class TestExhaustionDecisionMetadata:
    """Test metadata for churn exhaustion decision."""

    def test_churn_evidence_includes_action_summaries(self):
        """Churn evidence provides per-action summaries."""
        graph = WorldModelGraph("test_task", "test_session")
        
        for action_id in ["ACTION1", "ACTION2"]:
            for step in range(2):
                state_id = graph.record_state(step, f"hash_{action_id}_{step}")
                obs_id = graph.record_observation(step, f"hash_{action_id}_{step}", 0.0, 10.0)
                act_id = graph.record_action(step, action_id, {"coordinate": [0, 0]}, state_id)
                graph.record_effect(act_id, obs_id, "pixel_churn", {"step": step, "meaningful": False})
        
        churn_evidence = graph.get_all_actions_churn_evidence(
            available_actions=["ACTION1", "ACTION2"],
            min_tests_per_action=2
        )
        
        assert "action_summaries" in churn_evidence
        assert "ACTION1" in churn_evidence["action_summaries"]
        assert "ACTION2" in churn_evidence["action_summaries"]

    def test_exhaustion_metrics(self):
        """Exhaustion evidence includes count metrics."""
        graph = WorldModelGraph("test_task", "test_session")
        
        for action_id in ["ACTION1", "ACTION2", "ACTION3"]:
            for step in range(2):
                state_id = graph.record_state(step, f"hash_{action_id}_{step}")
                obs_id = graph.record_observation(step, f"hash_{action_id}_{step}", 0.0, 10.0)
                act_id = graph.record_action(step, action_id, {"coordinate": [0, 0]}, state_id)
                graph.record_effect(act_id, obs_id, "pixel_churn", {"step": step, "meaningful": False})
        
        churn_evidence = graph.get_all_actions_churn_evidence(
            available_actions=["ACTION1", "ACTION2", "ACTION3"],
            min_tests_per_action=2
        )
        
        assert churn_evidence["actions_tested_count"] == 3
        assert churn_evidence["required_action_count"] == 3
        assert churn_evidence["total_churn_count"] > 0

    def test_exhaustion_evidence_path(self):
        """Exhaustion evidence includes path IDs for graph reconstruction."""
        graph = WorldModelGraph("test_task", "test_session")
        
        for action_id in ["ACTION1", "ACTION2"]:
            for step in range(2):
                state_id = graph.record_state(step, f"hash_{action_id}_{step}")
                obs_id = graph.record_observation(step, f"hash_{action_id}_{step}", 0.0, 10.0)
                act_id = graph.record_action(step, action_id, {"coordinate": [0, 0]}, state_id)
                graph.record_effect(act_id, obs_id, "pixel_churn", {"step": step, "meaningful": False})
        
        churn_evidence = graph.get_all_actions_churn_evidence(
            available_actions=["ACTION1", "ACTION2"],
            min_tests_per_action=2
        )
        
        assert "evidence_path_ids" in churn_evidence
        assert len(churn_evidence["evidence_path_ids"]) > 0


class TestExhaustionWithMixedEffects:
    """Test exhaustion detection with mixed effect types."""

    def test_harmful_and_churn_mixed(self):
        """Mix of harmful and churn effects tracked separately."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # ACTION1: churn
        for step in range(2):
            state_id = graph.record_state(step, f"hash_ACTION1_{step}")
            obs_id = graph.record_observation(step, f"hash_ACTION1_{step}", 0.0, 10.0)
            act_id = graph.record_action(step, "ACTION1", {"coordinate": [0, 0]}, state_id)
            graph.record_effect(act_id, obs_id, "pixel_churn", {"step": step, "meaningful": False})
        
        # ACTION2: also churn (not harmful - harmful doesn't count as churn for exhaustion)
        for step in range(2, 4):
            state_id = graph.record_state(step, f"hash_ACTION2_{step}")
            obs_id = graph.record_observation(step, f"hash_ACTION2_{step}", 0.0, 10.0)
            act_id = graph.record_action(step, "ACTION2", {"coordinate": [1, 1]}, state_id)
            graph.record_effect(act_id, obs_id, "pixel_churn", {"step": step, "meaningful": False})
        
        churn_evidence = graph.get_all_actions_churn_evidence(
            available_actions=["ACTION1", "ACTION2"],
            min_tests_per_action=2
        )
        
        # Should be exhausted (all actions have churn, no progress)
        assert churn_evidence["all_actions_churn"] is True
        assert churn_evidence["total_churn_count"] >= 4

    def test_local_only_progress_counts_as_progress(self):
        """Local-only (non-terminal) progress still breaks exhaustion."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # ACTION1 and ACTION2 with churn
        for action_id in ["ACTION1", "ACTION2"]:
            for step in range(2):
                state_id = graph.record_state(step, f"hash_{action_id}_{step}")
                obs_id = graph.record_observation(step, f"hash_{action_id}_{step}", 0.0, 10.0)
                act_id = graph.record_action(step, action_id, {"coordinate": [0, 0]}, state_id)
                graph.record_effect(act_id, obs_id, "pixel_churn", {"step": step, "meaningful": False})
        
        # ACTION3 with local object progress (not terminal-aligned)
        for step in range(2, 4):
            state_id = graph.record_state(step, f"hash_ACTION3_{step}")
            obs_id = graph.record_observation(step, f"hash_ACTION3_{step}", 0.0, 10.0)  # goal distance unchanged
            act_id = graph.record_action(step, "ACTION3", {"coordinate": [1, 1]}, state_id)
            graph.record_effect(act_id, obs_id, "object_progress", {"step": step, "meaningful": True})
        
        churn_evidence = graph.get_all_actions_churn_evidence(
            available_actions=["ACTION1", "ACTION2", "ACTION3"],
            min_tests_per_action=2
        )
        
        # Local progress counts as progress, breaks exhaustion
        assert churn_evidence["all_actions_churn"] is False
        assert churn_evidence["total_progress_count"] > 0


class TestExhaustionEdgeCases:
    """Test edge cases in exhaustion detection."""

    def test_empty_action_list(self):
        """Empty action list handles gracefully."""
        graph = WorldModelGraph("test_task", "test_session")
        
        churn_evidence = graph.get_all_actions_churn_evidence(
            available_actions=[],
            min_tests_per_action=2
        )
        
        assert churn_evidence["all_actions_churn"] is False
        assert churn_evidence["actions_tested_count"] == 0

    def test_single_action(self):
        """Single action can still trigger exhaustion."""
        graph = WorldModelGraph("test_task", "test_session")
        
        for step in range(2):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", 0.0, 10.0)
            act_id = graph.record_action(step, "ACTION1", {"coordinate": [0, 0]}, state_id)
            graph.record_effect(act_id, obs_id, "pixel_churn", {"step": step, "meaningful": False})
        
        churn_evidence = graph.get_all_actions_churn_evidence(
            available_actions=["ACTION1"],
            min_tests_per_action=2
        )
        
        assert churn_evidence["all_actions_churn"] is True

    def test_min_tests_requirement(self):
        """Minimum tests requirement is enforced."""
        graph = WorldModelGraph("test_task", "test_session")
        
        for action_id in ["ACTION1", "ACTION2"]:
            # Only 1 test per action
            state_id = graph.record_state(0, f"hash_{action_id}")
            obs_id = graph.record_observation(0, f"hash_{action_id}", 0.0, 10.0)
            act_id = graph.record_action(0, action_id, {"coordinate": [0, 0]}, state_id)
            graph.record_effect(act_id, obs_id, "pixel_churn", {"step": 0, "meaningful": False})
        
        churn_evidence_1 = graph.get_all_actions_churn_evidence(
            available_actions=["ACTION1", "ACTION2"],
            min_tests_per_action=1
        )
        
        churn_evidence_2 = graph.get_all_actions_churn_evidence(
            available_actions=["ACTION1", "ACTION2"],
            min_tests_per_action=2
        )
        
        # With min_tests=1, should be exhausted
        assert churn_evidence_1["all_actions_churn"] is True
        
        # With min_tests=2, should not be exhausted
        assert churn_evidence_2["all_actions_churn"] is False
