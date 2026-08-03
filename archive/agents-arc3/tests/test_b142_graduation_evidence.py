"""Tests for B142: Chunk Graduation Must Respect Evidence Floor."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from agents.arc3.solver import SolveEngine, PlanChunk, VictoryCondition, VictoryType


@pytest.fixture
def mock_brain():
    """Create a mock brain client."""
    return MagicMock()


@pytest.fixture
def mock_llm():
    """Create a mock LLM client."""
    return MagicMock()


@pytest.fixture
def solver(mock_brain, mock_llm):
    """Create a SolveEngine instance."""
    return SolveEngine(mock_brain, mock_llm, "test_session")


class TestEvidenceFloor:
    """Test the evidence floor gating."""

    def test_evidence_floor_caps_high_confidence_low_evidence(self, solver):
        """Evidence floor should cap graduation when evidence < 0.3 and progress == 0."""
        # Scenario: high player/goal confidence, but zero evidence and no progress
        graduation = solver.plan_chunker._graduation_assessment(
            player_role=MagicMock(confidence=0.90, estimated_position={"row": 5, "col": 5}),
            goal_role=MagicMock(confidence=0.83, estimated_position={"row": 10, "col": 10}),
            hypothesis_context={
                "action_facts": [],  # No evidence
                "path_hypotheses": [],
                "action_coverage": {"tested_count": 2, "untested_count": 2, "initial_exploration_complete": False},
                "loop_detected": False,
            },
            available_actions=["ACTION1", "ACTION2", "ACTION3", "ACTION4"],
            chunk_progress=0.0,  # No progress
            steps_using_chunk=5,  # Used for 5 steps
        )

        # Graduation should be capped
        assert graduation["evidence_floor_applied"] is True
        assert graduation["graduation_capped_reason"] == "evidence_floor"
        assert graduation["score"] <= 0.4  # Capped at max(0.4, evidence * 2)
        assert graduation["pre_cap_score"] > 0.4  # Original score was higher

    def test_no_evidence_floor_when_progress_exists(self, solver):
        """Evidence floor should NOT apply when chunk has made progress."""
        graduation = solver.plan_chunker._graduation_assessment(
            player_role=MagicMock(confidence=0.90, estimated_position={"row": 5, "col": 5}),
            goal_role=MagicMock(confidence=0.83, estimated_position={"row": 10, "col": 10}),
            hypothesis_context={
                "action_facts": [],  # No evidence
                "path_hypotheses": [],
                "action_coverage": {"tested_count": 2, "untested_count": 2},
                "loop_detected": False,
            },
            available_actions=["ACTION1", "ACTION2", "ACTION3", "ACTION4"],
            chunk_progress=0.3,  # Has progress
            steps_using_chunk=5,
        )

        # Evidence floor should NOT apply
        assert graduation["evidence_floor_applied"] is False

    def test_no_evidence_floor_when_evidence_sufficient(self, solver):
        """Evidence floor should NOT apply when evidence >= 0.3."""
        graduation = solver.plan_chunker._graduation_assessment(
            player_role=MagicMock(confidence=0.90, estimated_position={"row": 5, "col": 5}),
            goal_role=MagicMock(confidence=0.83, estimated_position={"row": 10, "col": 10}),
            hypothesis_context={
                "action_facts": [
                    {"fact_type": "deterministic_effect", "value_status": "valuable"},
                    {"fact_type": "deterministic_effect", "value_status": "valuable"},
                    {"fact_type": "deterministic_effect", "value_status": "valuable"},
                ],  # Good evidence
                "path_hypotheses": [],
                "action_coverage": {"tested_count": 2, "untested_count": 2},
                "loop_detected": False,
            },
            available_actions=["ACTION1", "ACTION2", "ACTION3", "ACTION4"],
            chunk_progress=0.0,
            steps_using_chunk=5,
        )

        # Evidence floor should NOT apply
        assert graduation["evidence_floor_applied"] is False

    def test_no_evidence_floor_with_fewer_than_3_steps(self, solver):
        """Evidence floor should NOT apply if chunk used for < 3 steps."""
        graduation = solver.plan_chunker._graduation_assessment(
            player_role=MagicMock(confidence=0.90, estimated_position={"row": 5, "col": 5}),
            goal_role=MagicMock(confidence=0.83, estimated_position={"row": 10, "col": 10}),
            hypothesis_context={
                "action_facts": [],  # No evidence
                "path_hypotheses": [],
                "action_coverage": {"tested_count": 2, "untested_count": 2},
                "loop_detected": False,
            },
            available_actions=["ACTION1", "ACTION2", "ACTION3", "ACTION4"],
            chunk_progress=0.0,
            steps_using_chunk=2,  # Only 2 steps
        )

        # Evidence floor should NOT apply yet
        assert graduation["evidence_floor_applied"] is False


class TestProgressDecay:
    """Test the progress-decay penalty."""

    def test_progress_decay_degrades_graduation(self, solver):
        """Graduation should decay with consecutive zero-reward steps."""
        graduation = solver.plan_chunker._graduation_assessment(
            player_role=MagicMock(confidence=0.90, estimated_position={"row": 5, "col": 5}),
            goal_role=MagicMock(confidence=0.83, estimated_position={"row": 10, "col": 10}),
            hypothesis_context={
                "action_facts": [
                    {"fact_type": "deterministic_effect", "value_status": "valuable"},
                ],
                "path_hypotheses": [],
                "action_coverage": {"tested_count": 2, "untested_count": 2},
                "loop_detected": False,
            },
            available_actions=["ACTION1", "ACTION2", "ACTION3", "ACTION4"],
            chunk_progress=0.5,
            steps_using_chunk=5,
            consecutive_zero_reward_steps=5,  # 5 consecutive zero-reward steps
        )

        # Graduation should be degraded
        expected_decay = 0.05 * 5  # 0.25
        assert graduation["progress_decay_applied"] == expected_decay
        assert graduation["score"] < graduation["pre_cap_score"]

    def test_progress_decay_floor_at_0_2(self, solver):
        """Progress decay should be floored at 0.2."""
        graduation = solver.plan_chunker._graduation_assessment(
            player_role=MagicMock(confidence=0.90, estimated_position={"row": 5, "col": 5}),
            goal_role=MagicMock(confidence=0.83, estimated_position={"row": 10, "col": 10}),
            hypothesis_context={
                "action_facts": [],
                "path_hypotheses": [],
                "action_coverage": {"tested_count": 2, "untested_count": 2},
                "loop_detected": False,
            },
            available_actions=["ACTION1", "ACTION2", "ACTION3", "ACTION4"],
            chunk_progress=0.0,
            steps_using_chunk=3,
            consecutive_zero_reward_steps=20,  # Extreme decay
        )

        # Score should be floored at 0.2
        assert graduation["score"] >= 0.2


class TestGraduationReevaluation:
    """Test the reevaluate_chunk_graduation method."""

    def test_reevaluate_updates_active_chunk_score(self, solver):
        """Reevaluation should update the active chunk's graduation score."""
        # Create an active chunk with initial high score
        solver._active_chunk = PlanChunk(
            description="Test chunk",
            estimated_actions=["ACTION1"],
            graduation_score=0.87,  # High initial score
        )
        solver._active_chunk.progress_score = 0.0
        solver._active_chunk.steps_executed = 5

        # Mock the dissonance detector's zero progress streak
        solver.dissonance_detector._zero_progress_streak = 5

        result = solver.reevaluate_chunk_graduation({
            "action_facts": [],
            "path_hypotheses": [],
            "action_coverage": {},
        })

        # Graduation should have been re-evaluated and lowered
        assert result["new_score"] < result["original_score"]
        assert solver._active_chunk.graduation_score == result["new_score"]

    def test_reevaluate_returns_trace_fields(self, solver):
        """Reevaluation should return all required trace fields."""
        solver._active_chunk = PlanChunk(
            description="Test chunk",
            estimated_actions=["ACTION1"],
            graduation_score=0.87,
        )
        solver._active_chunk.progress_score = 0.0
        solver._active_chunk.steps_executed = 5
        solver.dissonance_detector._zero_progress_streak = 5

        result = solver.reevaluate_chunk_graduation({
            "action_facts": [],
            "path_hypotheses": [],
            "action_coverage": {},
        })

        # Should include all trace fields
        assert "original_score" in result
        assert "new_score" in result
        assert "graduation_capped_reason" in result
        assert "evidence_floor_applied" in result
        assert "progress_decay_applied" in result
        assert "evidence_score" in result
        assert "chunk_progress" in result
        assert "steps_using_chunk" in result
        assert "consecutive_zero_reward" in result

    def test_reevaluate_no_active_chunk_returns_empty(self, solver):
        """Reevaluation with no active chunk should return empty dict."""
        solver._active_chunk = None

        result = solver.reevaluate_chunk_graduation({"action_facts": []})

        assert result == {}


class TestDissonanceTrigger:
    """Test that graduation drop triggers dissonance."""

    def test_graduation_drop_below_0_5_triggers_dissonance(self, solver):
        """When graduation drops below 0.5, dissonance should trigger."""
        # Create chunk with initial score > 0.5
        solver._active_chunk = PlanChunk(
            description="Test chunk",
            estimated_actions=["ACTION1"],
            graduation_score=0.87,
        )
        solver._active_chunk.progress_score = 0.0
        solver._active_chunk.steps_executed = 5
        solver.dissonance_detector._zero_progress_streak = 10

        result = solver.reevaluate_chunk_graduation({
            "action_facts": [],
            "path_hypotheses": [],
            "action_coverage": {},
        })

        # With 10 zero-reward steps, decay = 0.50, so 0.87 - 0.50 = 0.37 < 0.5
        assert result["new_score"] < 0.5
        assert result["graduation_capped_reason"] is not None


class TestAcceptanceCriteria:
    """Test all acceptance criteria."""

    def test_criterion_evidence_floor_cap(self, solver):
        """Chunk with evidence < 0.3, progress == 0, >= 3 steps has graduation ≤ 0.4."""
        graduation = solver.plan_chunker._graduation_assessment(
            player_role=MagicMock(confidence=0.90, estimated_position={"row": 5, "col": 5}),
            goal_role=MagicMock(confidence=0.83, estimated_position={"row": 10, "col": 10}),
            hypothesis_context={
                "action_facts": [],
                "path_hypotheses": [],
                "action_coverage": {"tested_count": 2, "untested_count": 2},
                "loop_detected": False,
            },
            available_actions=["ACTION1", "ACTION2", "ACTION3", "ACTION4"],
            chunk_progress=0.0,
            steps_using_chunk=3,
        )

        assert graduation["evidence_floor_applied"] is True
        assert graduation["score"] <= 0.4

    def test_criterion_no_false_positives(self, solver):
        """High evidence + progress should NOT be penalized."""
        graduation = solver.plan_chunker._graduation_assessment(
            player_role=MagicMock(confidence=0.90, estimated_position={"row": 5, "col": 5}),
            goal_role=MagicMock(confidence=0.83, estimated_position={"row": 10, "col": 10}),
            hypothesis_context={
                "action_facts": [
                    {"fact_type": "deterministic_effect", "value_status": "valuable"},
                    {"fact_type": "deterministic_effect", "value_status": "valuable"},
                ],
                "path_hypotheses": [],
                "action_coverage": {"tested_count": 3, "untested_count": 1},
                "loop_detected": False,
            },
            available_actions=["ACTION1", "ACTION2", "ACTION3", "ACTION4"],
            chunk_progress=0.5,
            steps_using_chunk=5,
        )

        # Should not be penalized
        assert graduation["evidence_floor_applied"] is False
        assert graduation["graduation_capped_reason"] is None
