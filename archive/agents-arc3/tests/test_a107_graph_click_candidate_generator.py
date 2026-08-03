"""Test A107 — Graph click candidate generator."""

import pytest
from agents.arc3.click_candidates import (
    ClickableCandidate,
    ClickCandidateGenerator,
    ClickCandidateStore,
)
from agents.arc3.world_model import WorldModelGraph


class TestClickableCandidate:
    """Test ClickableCandidate dataclass."""

    def test_clickable_candidate_creation(self):
        """Create a basic ClickableCandidate."""
        cand = ClickableCandidate(
            id="click-test-1",
            x=10,
            y=12,
            color=2,
            role="object_center",
            confidence=0.7,
        )
        
        assert cand.id == "click-test-1"
        assert cand.x == 10
        assert cand.y == 12
        assert cand.color == 2
        assert cand.role == "object_center"
        assert cand.confidence == 0.7

    def test_clickable_candidate_to_dict(self):
        """Convert ClickableCandidate to dict."""
        cand = ClickableCandidate(
            id="click-test-1",
            x=10,
            y=12,
            color=2,
            role="framed_center",
            confidence=0.8,
            rank=1,
        )
        
        cand_dict = cand.to_dict()
        
        assert cand_dict["id"] == "click-test-1"
        assert cand_dict["x"] == 10
        assert cand_dict["y"] == 12
        assert cand_dict["color"] == 2
        assert cand_dict["role"] == "framed_center"
        assert cand_dict["confidence"] == 0.8


class TestClickCandidateGenerator:
    """Test ClickCandidateGenerator."""

    def test_generate_from_goal_hypothesis(self):
        """Generate candidates from goal hypothesis."""
        graph = WorldModelGraph("test", "session")
        generator = ClickCandidateGenerator(graph)
        
        goal_hypothesis = {
            "id": "goal-1",
            "goal_type": "color_correspondence",
            "target_objects": [
                {"id": "obj-1", "center_x": 10, "center_y": 12, "color": 2},
                {"id": "obj-2", "center_x": 20, "center_y": 25, "color": 3},
            ],
        }
        
        candidates = generator.generate(
            active_goal_hypotheses=[goal_hypothesis],
            limit=10
        )
        
        assert len(candidates) == 2
        assert candidates[0].x == 10
        assert candidates[0].y == 12
        assert candidates[0].role == "goal_target_center"
        assert candidates[0].confidence >= 0.7

    def test_generate_from_mechanic_objects(self):
        """Generate candidates from mechanic graph objects."""
        graph = WorldModelGraph("test", "session")
        generator = ClickCandidateGenerator(graph)
        
        snapshot = {
            "objects": [
                {
                    "id": "obj-1",
                    "center_x": 15,
                    "center_y": 18,
                    "color": 2,
                    "is_framed": False,
                },
                {
                    "id": "obj-2",
                    "center_x": 25,
                    "center_y": 30,
                    "color": 3,
                    "is_framed": True,
                },
            ],
            "relations": [],
            "panels": [],
        }
        
        candidates = generator.generate(mechanic_graph_snapshot=snapshot, limit=10)
        
        assert len(candidates) > 0
        
        # Check that we have object centers
        center_roles = [c.role for c in candidates if "center" in c.role]
        assert len(center_roles) > 0

    def test_deduplication_by_coordinate(self):
        """Candidates should be deduplicated by coordinate."""
        graph = WorldModelGraph("test", "session")
        generator = ClickCandidateGenerator(graph)
        
        # Two hypotheses pointing to same coordinate
        hypotheses = [
            {
                "id": "goal-1",
                "goal_type": "color_correspondence",
                "target_objects": [
                    {"id": "obj-1", "center_x": 10, "center_y": 12, "color": 2},
                ],
            },
            {
                "id": "goal-2",
                "goal_type": "pattern_completion",
                "target_objects": [
                    {"id": "obj-1", "center_x": 10, "center_y": 12, "color": 2},
                ],
            },
        ]
        
        candidates = generator.generate(active_goal_hypotheses=hypotheses, limit=10)
        
        # Should have only 1 candidate at (10, 12), not 2
        coords = set((c.x, c.y) for c in candidates)
        assert len(coords) == 1
        assert (10, 12) in coords

    def test_confidence_based_ranking(self):
        """Candidates should be ranked by confidence."""
        graph = WorldModelGraph("test", "session")
        generator = ClickCandidateGenerator(graph)
        
        snapshot = {
            "objects": [
                {
                    "id": "obj-low",
                    "center_x": 10,
                    "center_y": 12,
                    "color": 2,
                    "is_framed": False,  # Will get 0.5 confidence
                },
                {
                    "id": "obj-high",
                    "center_x": 20,
                    "center_y": 25,
                    "color": 3,
                    "is_framed": True,  # Will get 0.8 confidence
                },
            ],
            "relations": [],
            "panels": [],
        }
        
        candidates = generator.generate(mechanic_graph_snapshot=snapshot, limit=10)
        
        # First candidate should have higher confidence
        assert candidates[0].confidence >= candidates[-1].confidence

    def test_bounded_limit(self):
        """Candidates should be bounded by limit and MAX_CANDIDATES_PER_FRAME."""
        graph = WorldModelGraph("test", "session")
        generator = ClickCandidateGenerator(graph)
        
        # Create many objects
        objects = [
            {
                "id": f"obj-{i}",
                "center_x": i * 10,
                "center_y": i * 10,
                "color": i % 8,
                "is_framed": False,
            }
            for i in range(50)
        ]
        
        snapshot = {
            "objects": objects,
            "relations": [],
            "panels": [],
        }
        
        # Request more than max
        candidates = generator.generate(mechanic_graph_snapshot=snapshot, limit=100)
        
        # Should be capped at MAX_CANDIDATES_PER_FRAME
        assert len(candidates) <= ClickCandidateGenerator.MAX_CANDIDATES_PER_FRAME


class TestClickCandidateStore:
    """Test ClickCandidateStore."""

    def test_upsert_and_retrieve_candidates(self):
        """Store and retrieve candidates."""
        store = ClickCandidateStore()
        
        candidates = [
            ClickableCandidate(
                id="click-1",
                x=10,
                y=12,
                role="object_center",
                confidence=0.7,
            ),
            ClickableCandidate(
                id="click-2",
                x=20,
                y=25,
                role="framed_center",
                confidence=0.8,
            ),
        ]
        
        store.upsert_candidates("frame_hash_1", candidates)
        
        retrieved = store.get_candidates("frame_hash_1")
        assert len(retrieved) == 2
        assert retrieved[0]["id"] in ("click-1", "click-2")

    def test_get_candidate_by_id(self):
        """Retrieve specific candidate by id."""
        store = ClickCandidateStore()
        
        candidates = [
            ClickableCandidate(
                id="click-1",
                x=10,
                y=12,
                role="object_center",
                confidence=0.7,
            ),
        ]
        
        store.upsert_candidates("frame_hash_1", candidates)
        
        cand = store.get_candidate_by_id("click-1")
        assert cand is not None
        assert cand["x"] == 10
        assert cand["y"] == 12

    def test_filter_by_goal_type(self):
        """Filter candidates by goal type."""
        store = ClickCandidateStore()
        
        candidates = [
            ClickableCandidate(
                id="click-1",
                x=10,
                y=12,
                role="goal_target_center",
                confidence=0.7,
                goal_type="color_correspondence",
            ),
            ClickableCandidate(
                id="click-2",
                x=20,
                y=25,
                role="mismatch_cell",
                confidence=0.6,
                goal_type="pattern_completion",
            ),
        ]
        
        store.upsert_candidates("frame_hash_1", candidates)
        
        filtered = store.get_candidates(
            "frame_hash_1",
            goal_type="color_correspondence"
        )
        
        assert len(filtered) == 1
        assert filtered[0]["id"] == "click-1"


class TestWorldModelClickCandidates:
    """Test click candidate methods in WorldModelGraph."""

    def test_upsert_click_candidates(self):
        """Upsert click candidates into world model."""
        graph = WorldModelGraph("test", "session")
        
        candidates = [
            {
                "id": "click-1",
                "x": 10,
                "y": 12,
                "color": 2,
                "role": "object_center",
                "confidence": 0.7,
                "rank": 1,
            },
        ]
        
        graph.upsert_click_candidates(candidates, "frame_hash_1")
        
        # Verify nodes were created
        candidate_nodes = [n for n in graph.nodes.values() if n.label == "ClickableCandidate"]
        assert len(candidate_nodes) == 1
        assert candidate_nodes[0].props["x"] == 10
        assert candidate_nodes[0].props["y"] == 12

    def test_get_click_candidates_from_graph(self):
        """Retrieve click candidates from graph."""
        graph = WorldModelGraph("test", "session")
        
        candidates = [
            {
                "id": "click-1",
                "x": 10,
                "y": 12,
                "role": "object_center",
                "confidence": 0.7,
                "rank": 1,
                "goal_type": None,
            },
            {
                "id": "click-2",
                "x": 20,
                "y": 25,
                "role": "framed_center",
                "confidence": 0.8,
                "rank": 2,
                "goal_type": None,
            },
        ]
        
        graph.upsert_click_candidates(candidates, "frame_hash_1")
        
        retrieved = graph.get_click_candidates(limit=10)
        assert len(retrieved) > 0

    def test_get_click_candidate_by_id_from_graph(self):
        """Retrieve specific candidate from graph."""
        graph = WorldModelGraph("test", "session")
        
        candidates = [
            {
                "id": "click-1",
                "x": 10,
                "y": 12,
                "role": "object_center",
                "confidence": 0.7,
                "rank": 1,
                "goal_type": None,
            },
        ]
        
        graph.upsert_click_candidates(candidates, "frame_hash_1")
        
        cand = graph.get_click_candidate_by_id("click-1")
        assert cand is not None
        assert cand["x"] == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
