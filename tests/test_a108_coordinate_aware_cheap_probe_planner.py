"""Test A108 — Coordinate-aware cheap probe planner."""

import pytest
from agents.arc3.world_model import WorldModelGraph, build_action_identity
from agents.arc3.world_model_planner import WorldModelPlanner, PlanMode
from agents.arc3.click_candidates import ClickableCandidate


class TestClickProbeGeneration:
    """Test click probe candidate generation."""

    def test_generate_click_probe_candidates(self):
        """Generate click probe candidates from world model."""
        planner = WorldModelPlanner()
        graph = WorldModelGraph("test", "session")
        
        # Upsert some click candidates
        candidates = [
            {
                "id": "click-1",
                "x": 10,
                "y": 12,
                "role": "object_center",
                "confidence": 0.7,
                "rank": 1,
            },
            {
                "id": "click-2",
                "x": 20,
                "y": 25,
                "role": "framed_center",
                "confidence": 0.8,
                "rank": 2,
            },
        ]
        
        graph.upsert_click_candidates(candidates, "frame_hash_1")
        
        # Generate plan candidates
        plan_candidates = planner.generate_click_probe_candidates(
            world_model=graph,
            available_actions=["ACTION6"],
        )
        
        assert len(plan_candidates) == 2
        assert plan_candidates[0].mode == PlanMode.CLICK_PROBE
        assert plan_candidates[0].action_id == "ACTION6"
        # Should be ordered by confidence (click-2 has 0.8 > click-1's 0.7)
        assert plan_candidates[0].args["x"] == 20
        assert plan_candidates[0].args["y"] == 25
        assert plan_candidates[1].args["x"] == 10
        assert plan_candidates[1].args["y"] == 12

    def test_action_identity_generated(self):
        """Click probe candidates should have action_identity."""
        planner = WorldModelPlanner()
        graph = WorldModelGraph("test", "session")
        
        candidates = [
            {
                "id": "click-1",
                "x": 10,
                "y": 12,
                "role": "object_center",
                "confidence": 0.7,
                "rank": 1,
            },
        ]
        
        graph.upsert_click_candidates(candidates, "frame_hash_1")
        
        plan_candidates = planner.generate_click_probe_candidates(
            world_model=graph,
            available_actions=["ACTION6"],
        )
        
        assert len(plan_candidates) == 1
        assert plan_candidates[0].action_identity == "ACTION6@10,12"

    def test_no_candidates_for_non_click_actions(self):
        """Should not generate click candidates for non-ACTION6."""
        planner = WorldModelPlanner()
        graph = WorldModelGraph("test", "session")
        
        plan_candidates = planner.generate_click_probe_candidates(
            world_model=graph,
            available_actions=["ACTION0", "ACTION1"],
        )
        
        assert len(plan_candidates) == 0

    def test_ranked_by_confidence(self):
        """Click probe candidates should be ranked by confidence."""
        planner = WorldModelPlanner()
        graph = WorldModelGraph("test", "session")
        
        candidates = [
            {
                "id": "click-low",
                "x": 10,
                "y": 12,
                "role": "object_center",
                "confidence": 0.5,
                "rank": 1,
            },
            {
                "id": "click-high",
                "x": 20,
                "y": 25,
                "role": "framed_center",
                "confidence": 0.9,
                "rank": 2,
            },
        ]
        
        graph.upsert_click_candidates(candidates, "frame_hash_1")
        
        plan_candidates = planner.generate_click_probe_candidates(
            world_model=graph,
            available_actions=["ACTION6"],
        )
        
        # Higher confidence candidates should come first
        assert plan_candidates[0].click_candidate_id == "click-high"
        assert plan_candidates[1].click_candidate_id == "click-low"


class TestClickProbeSelection:
    """Test click probe action selection."""

    def test_suggest_first_untried_candidate(self):
        """suggest_click_probe_action should return first untried candidate."""
        planner = WorldModelPlanner()
        graph = WorldModelGraph("test", "session")
        
        candidates = [
            {
                "id": "click-1",
                "x": 10,
                "y": 12,
                "role": "object_center",
                "confidence": 0.7,
                "rank": 1,
            },
            {
                "id": "click-2",
                "x": 20,
                "y": 25,
                "role": "framed_center",
                "confidence": 0.6,
                "rank": 2,
            },
        ]
        
        graph.upsert_click_candidates(candidates, "frame_hash_1")
        
        selected = planner.suggest_click_probe_action(
            world_model=graph,
            available_actions=["ACTION6"],
        )
        
        assert selected is not None
        # Should return the first candidate (higher confidence 0.7 > 0.6)
        assert selected.args["x"] == 10
        assert selected.args["y"] == 12
        assert selected.action_identity == "ACTION6@10,12"

    def test_skip_quarantined_action_identity(self):
        """suggest_click_probe_action should skip quarantined coordinates."""
        planner = WorldModelPlanner()
        graph = WorldModelGraph("test", "session")
        
        candidates = [
            {
                "id": "click-1",
                "x": 10,
                "y": 12,
                "role": "object_center",
                "confidence": 0.7,
                "rank": 1,
            },
            {
                "id": "click-2",
                "x": 20,
                "y": 25,
                "role": "framed_center",
                "confidence": 0.6,
                "rank": 2,
            },
        ]
        
        graph.upsert_click_candidates(candidates, "frame_hash_1")
        
        # Quarantine the first candidate
        selected = planner.suggest_click_probe_action(
            world_model=graph,
            available_actions=["ACTION6"],
            quarantined_action_identities={"ACTION6@10,12"},
        )
        
        # Should return second candidate
        assert selected is not None
        assert selected.args["x"] == 20
        assert selected.args["y"] == 25

    def test_return_none_when_all_exhausted(self):
        """suggest_click_probe_action should return None when all candidates exhausted."""
        planner = WorldModelPlanner()
        graph = WorldModelGraph("test", "session")
        
        candidates = [
            {
                "id": "click-1",
                "x": 10,
                "y": 12,
                "role": "object_center",
                "confidence": 0.7,
                "rank": 1,
            },
        ]
        
        graph.upsert_click_candidates(candidates, "frame_hash_1")
        
        # Quarantine all candidates
        selected = planner.suggest_click_probe_action(
            world_model=graph,
            available_actions=["ACTION6"],
            quarantined_action_identities={"ACTION6@10,12"},
        )
        
        # Should return None
        assert selected is None

    def test_no_candidates_available(self):
        """suggest_click_probe_action should return None when no candidates."""
        planner = WorldModelPlanner()
        graph = WorldModelGraph("test", "session")
        
        # No candidates in graph
        selected = planner.suggest_click_probe_action(
            world_model=graph,
            available_actions=["ACTION6"],
        )
        
        assert selected is None


class TestClickProbeCoordinateAwareness:
    """Test that click probes are truly coordinate-aware."""

    def test_different_coordinates_different_identities(self):
        """ACTION6@10,12 and ACTION6@20,25 should be different identities."""
        planner = WorldModelPlanner()
        graph = WorldModelGraph("test", "session")
        
        candidates = [
            {
                "id": "click-1",
                "x": 10,
                "y": 12,
                "role": "object_center",
                "confidence": 0.7,
                "rank": 1,
            },
            {
                "id": "click-2",
                "x": 20,
                "y": 25,
                "role": "framed_center",
                "confidence": 0.6,
                "rank": 2,
            },
        ]
        
        graph.upsert_click_candidates(candidates, "frame_hash_1")
        
        plan_candidates = planner.generate_click_probe_candidates(
            world_model=graph,
            available_actions=["ACTION6"],
        )
        
        # All should have different action_identities
        identities = [c.action_identity for c in plan_candidates]
        assert len(identities) == len(set(identities))
        assert "ACTION6@10,12" in identities
        assert "ACTION6@20,25" in identities

    def test_quarantine_one_coordinate_not_all(self):
        """Quarantining ACTION6@10,12 should not quarantine ACTION6@20,25."""
        planner = WorldModelPlanner()
        graph = WorldModelGraph("test", "session")
        
        candidates = [
            {
                "id": "click-1",
                "x": 10,
                "y": 12,
                "role": "object_center",
                "confidence": 0.7,
                "rank": 1,
            },
            {
                "id": "click-2",
                "x": 20,
                "y": 25,
                "role": "framed_center",
                "confidence": 0.6,
                "rank": 2,
            },
        ]
        
        graph.upsert_click_candidates(candidates, "frame_hash_1")
        
        # Quarantine only ACTION6@10,12
        selected = planner.suggest_click_probe_action(
            world_model=graph,
            available_actions=["ACTION6"],
            quarantined_action_identities={"ACTION6@10,12"},
        )
        
        # Should still get ACTION6@20,25
        assert selected is not None
        assert selected.action_identity == "ACTION6@20,25"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
