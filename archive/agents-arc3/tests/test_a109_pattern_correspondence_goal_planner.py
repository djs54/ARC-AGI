"""Test A109 — Pattern correspondence goal planner."""

import pytest
from agents.arc3.world_model import WorldModelGraph
from agents.arc3.world_model_planner import WorldModelPlanner, PlanMode


class TestPatternCorrespondenceRanking:
    """Test pattern correspondence candidate ranking."""

    def test_find_pattern_correspondence_candidates(self):
        """Find candidates ranked for pattern correspondence."""
        planner = WorldModelPlanner()
        graph = WorldModelGraph("test", "session")
        
        # Upsert goal hypothesis
        goal_hyp = {
            "id": "goal-1",
            "goal_type": "color_correspondence",
            "claim": "Test correspondence",
            "confidence": 0.8,
            "status": "active",
        }
        graph.upsert_goal_hypothesis(goal_hyp)
        
        # Upsert click candidates
        candidates = [
            {
                "id": "click-framed",
                "x": 10,
                "y": 12,
                "role": "framed_center",
                "confidence": 0.9,
                "rank": 1,
                "goal_type": "color_correspondence",
            },
            {
                "id": "click-mismatch",
                "x": 20,
                "y": 25,
                "role": "mismatch_cell",
                "confidence": 0.7,
                "rank": 2,
                "goal_type": "color_correspondence",
            },
        ]
        
        graph.upsert_click_candidates(candidates, "frame_hash_1")
        
        # Find pattern correspondence candidates
        corr_candidates = graph.find_pattern_correspondence_candidates(limit=10)
        
        assert len(corr_candidates) > 0
        # Framed center should be ranked higher than mismatch
        assert corr_candidates[0].get("role") == "framed_center"

    def test_find_panel_mismatches(self):
        """Find mismatch cells in pattern completion."""
        graph = WorldModelGraph("test", "session")
        
        # Upsert mismatch candidates
        candidates = [
            {
                "id": "click-mismatch-1",
                "x": 15,
                "y": 20,
                "role": "mismatch_cell",
                "confidence": 0.6,
                "rank": 1,
                "goal_type": "pattern_completion",
            },
        ]
        
        graph.upsert_click_candidates(candidates, "frame_hash_1")
        
        # Find mismatches
        mismatches = graph.find_panel_mismatches(limit=5)
        
        assert len(mismatches) == 1
        assert mismatches[0]["role"] == "mismatch_cell"

    def test_get_click_candidate_evidence(self):
        """Get evidence path for click candidate."""
        graph = WorldModelGraph("test", "session")
        
        # Upsert goal hypothesis
        goal_hyp = {
            "id": "goal-1",
            "goal_type": "color_correspondence",
            "claim": "Test",
            "confidence": 0.8,
            "status": "active",
        }
        graph.upsert_goal_hypothesis(goal_hyp)
        
        # Upsert candidate
        candidates = [
            {
                "id": "click-1",
                "x": 10,
                "y": 12,
                "role": "framed_center",
                "confidence": 0.8,
                "rank": 1,
            },
        ]
        
        graph.upsert_click_candidates(candidates, "frame_hash_1")
        
        # Get evidence
        evidence = graph.get_click_candidate_evidence("click-1")
        
        assert evidence is not None
        assert evidence["candidate_id"] == "click-1"
        assert evidence["candidate"]["x"] == 10


class TestPatternCorrespondencePlanning:
    """Test pattern correspondence planning."""

    def test_rank_pattern_correspondence_candidates(self):
        """Rank candidates for pattern correspondence."""
        planner = WorldModelPlanner()
        graph = WorldModelGraph("test", "session")
        
        # Upsert candidates
        candidates = [
            {
                "id": "click-framed",
                "x": 10,
                "y": 12,
                "role": "framed_center",
                "confidence": 0.9,
                "rank": 1,
                "goal_type": "color_correspondence",
            },
            {
                "id": "click-center",
                "x": 30,
                "y": 35,
                "role": "object_center",
                "confidence": 0.5,
                "rank": 2,
                "goal_type": "color_correspondence",
            },
        ]
        
        graph.upsert_click_candidates(candidates, "frame_hash_1")
        
        # Rank candidates
        ranked = planner.rank_pattern_correspondence_candidates(
            world_model=graph,
            available_actions=["ACTION6"],
        )
        
        assert len(ranked) > 0
        assert ranked[0].mode == PlanMode.CLICK_PROBE
        assert ranked[0].action_identity == "ACTION6@10,12"

    def test_prediction_generation(self):
        """Pattern correspondence candidates should have predictions."""
        planner = WorldModelPlanner()
        graph = WorldModelGraph("test", "session")
        
        # Upsert candidates
        candidates = [
            {
                "id": "click-1",
                "x": 10,
                "y": 12,
                "role": "framed_center",
                "confidence": 0.9,
                "rank": 1,
                "goal_type": "color_correspondence",
            },
        ]
        
        graph.upsert_click_candidates(candidates, "frame_hash_1")
        
        ranked = planner.rank_pattern_correspondence_candidates(
            world_model=graph,
            available_actions=["ACTION6"],
        )
        
        assert ranked[0].predicted_observation is not None
        assert ranked[0].predicted_observation["effect_class"] == "configuration_change"
        assert ranked[0].predicted_observation["goal_type"] == "color_correspondence"

    def test_falsification_condition_generation(self):
        """Pattern correspondence candidates should have falsification conditions."""
        planner = WorldModelPlanner()
        graph = WorldModelGraph("test", "session")
        
        # Upsert framed candidate
        framed = [
            {
                "id": "click-framed",
                "x": 10,
                "y": 12,
                "role": "framed_center",
                "confidence": 0.9,
                "rank": 1,
                "goal_type": "color_correspondence",
            },
        ]
        
        graph.upsert_click_candidates(framed, "frame_hash_1")
        
        ranked = planner.rank_pattern_correspondence_candidates(
            world_model=graph,
            available_actions=["ACTION6"],
        )
        
        assert ranked[0].falsification_condition is not None
        assert "No frame/center change" in ranked[0].falsification_condition

    def test_suggest_pattern_correspondence_action(self):
        """Suggest next pattern correspondence action."""
        planner = WorldModelPlanner()
        graph = WorldModelGraph("test", "session")
        
        # Upsert candidates
        candidates = [
            {
                "id": "click-1",
                "x": 10,
                "y": 12,
                "role": "framed_center",
                "confidence": 0.9,
                "rank": 1,
                "goal_type": "color_correspondence",
            },
        ]
        
        graph.upsert_click_candidates(candidates, "frame_hash_1")
        
        # Suggest action
        suggested = planner.suggest_pattern_correspondence_action(
            world_model=graph,
            available_actions=["ACTION6"],
        )
        
        assert suggested is not None
        assert suggested.args["x"] == 10
        assert suggested.args["y"] == 12

    def test_skip_quarantined_action_identity(self):
        """Should skip quarantined action identities."""
        planner = WorldModelPlanner()
        graph = WorldModelGraph("test", "session")
        
        # Upsert candidates
        candidates = [
            {
                "id": "click-1",
                "x": 10,
                "y": 12,
                "role": "framed_center",
                "confidence": 0.9,
                "rank": 1,
                "goal_type": "color_correspondence",
            },
            {
                "id": "click-2",
                "x": 20,
                "y": 25,
                "role": "mismatch_cell",
                "confidence": 0.7,
                "rank": 2,
                "goal_type": "color_correspondence",
            },
        ]
        
        graph.upsert_click_candidates(candidates, "frame_hash_1")
        
        # Quarantine first action identity
        suggested = planner.suggest_pattern_correspondence_action(
            world_model=graph,
            available_actions=["ACTION6"],
            quarantined_action_identities={"ACTION6@10,12"},
        )
        
        # Should get second candidate
        assert suggested is not None
        assert suggested.args["x"] == 20
        assert suggested.args["y"] == 25


class TestPatternCorrespondenceRoleRanking:
    """Test ranking by role priority."""

    def test_framed_ranked_higher_than_mismatch(self):
        """Framed centers should rank higher than mismatches."""
        planner = WorldModelPlanner()
        graph = WorldModelGraph("test", "session")
        
        candidates = [
            {
                "id": "click-mismatch",
                "x": 10,
                "y": 12,
                "role": "mismatch_cell",
                "confidence": 0.95,  # High confidence but lower priority role
                "rank": 1,
                "goal_type": "color_correspondence",
            },
            {
                "id": "click-framed",
                "x": 20,
                "y": 25,
                "role": "framed_center",
                "confidence": 0.7,  # Lower confidence but higher priority role
                "rank": 2,
                "goal_type": "color_correspondence",
            },
        ]
        
        graph.upsert_click_candidates(candidates, "frame_hash_1")
        
        # Find candidates (without planning, just raw order)
        raw_candidates = graph.find_pattern_correspondence_candidates(limit=10)
        
        # Framed should come first due to role priority
        assert raw_candidates[0]["role"] == "framed_center"

    def test_object_center_lower_priority(self):
        """Object centers should rank lower than framed."""
        planner = WorldModelPlanner()
        graph = WorldModelGraph("test", "session")
        
        candidates = [
            {
                "id": "click-object",
                "x": 10,
                "y": 12,
                "role": "object_center",
                "confidence": 0.9,
                "rank": 1,
                "goal_type": "color_correspondence",
            },
            {
                "id": "click-framed",
                "x": 20,
                "y": 25,
                "role": "framed_center",
                "confidence": 0.6,
                "rank": 2,
                "goal_type": "color_correspondence",
            },
        ]
        
        graph.upsert_click_candidates(candidates, "frame_hash_1")
        
        raw_candidates = graph.find_pattern_correspondence_candidates(limit=10)
        
        # Framed should still come first due to role priority
        assert raw_candidates[0]["role"] == "framed_center"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
