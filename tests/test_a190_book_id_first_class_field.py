"""A190 tests: PlanCandidate.book_id as a first-class field."""

from __future__ import annotations

import pytest

from agents.arc4.plan_generator import PlanGenerator, _CandidateRecord
from agents.arc4.types import GoalHypothesis, PerceptionSnapshot, PlanCandidate, ResolvedGoal, WorkflowState


def test_plan_candidate_book_id_from_metadata() -> None:
    """Test that PlanCandidate resolves book_id from metadata when not explicitly provided."""
    candidate = PlanCandidate(
        action_id="ACTION1",
        goal_id="goal_1",
        metadata={"book_id": "ACTION6@18,17"},
    )
    assert candidate.book_id == "ACTION6@18,17"


def test_plan_candidate_book_id_defaults_to_action_id() -> None:
    """Test that PlanCandidate falls back to action_id when no metadata book_id."""
    candidate = PlanCandidate(
        action_id="ACTION2",
        goal_id="goal_1",
        metadata={},
    )
    assert candidate.book_id == "ACTION2"


def test_plan_candidate_book_id_explicit() -> None:
    """Test that explicitly provided book_id is preserved, ignoring metadata."""
    candidate = PlanCandidate(
        action_id="ACTION1",
        goal_id="goal_1",
        book_id="EXPLICIT_ID",
        metadata={"book_id": "FROM_METADATA"},
    )
    assert candidate.book_id == "EXPLICIT_ID"


def test_plan_candidate_to_dict_includes_book_id() -> None:
    """Test that to_dict() includes the book_id field."""
    candidate = PlanCandidate(
        action_id="ACTION1",
        goal_id="goal_1",
        score=0.5,
        book_id="ACTION6@10,20",
        metadata={"goal_id": "goal_1"},
    )
    d = candidate.to_dict()
    assert "book_id" in d
    assert d["book_id"] == "ACTION6@10,20"


def test_plan_candidate_from_dict_with_book_id() -> None:
    """Test that from_dict() correctly reconstructs book_id."""
    d = {
        "action_id": "ACTION1",
        "goal_id": "goal_1",
        "score": 0.5,
        "rationale": "test",
        "book_id": "ACTION6@10,20",
        "metadata": {"goal_id": "goal_1"},
    }
    candidate = PlanCandidate.from_dict(d)
    assert candidate.book_id == "ACTION6@10,20"


def test_plan_candidate_from_dict_legacy_no_book_id() -> None:
    """Test backward compatibility: from_dict() without book_id key resolves via __post_init__."""
    d = {
        "action_id": "ACTION1",
        "goal_id": "goal_1",
        "score": 0.5,
        "rationale": "test",
        "metadata": {"goal_id": "goal_1", "book_id": "ACTION6@5,5"},
    }
    candidate = PlanCandidate.from_dict(d)
    assert candidate.book_id == "ACTION6@5,5"


def test_plan_candidate_round_trip() -> None:
    """Test that to_dict() and from_dict() preserve book_id exactly."""
    original = PlanCandidate(
        action_id="ACTION6",
        goal_id="goal_1",
        score=0.8,
        rationale="test",
        book_id="ACTION6@15,25",
        metadata={"goal_id": "goal_1"},
    )
    d = original.to_dict()
    reconstructed = PlanCandidate.from_dict(d)
    assert reconstructed.book_id == original.book_id
    assert reconstructed.action_id == original.action_id
    assert reconstructed.goal_id == original.goal_id


def test_to_plan_candidate_passes_book_id() -> None:
    """Test that _to_plan_candidate passes book_id explicitly from _CandidateRecord."""
    record = _CandidateRecord(
        action_id="ACTION6",
        book_id="ACTION6@30,40",
        payload={"x": 30, "y": 40},
        score=0.6,
        rationale="click target",
        expected_effect="grid change",
        predicted_outcome={"kind": "grid_change", "confidence": 0.5},
        metadata={"goal_id": "goal_1"},
    )
    candidate = PlanGenerator._to_plan_candidate(record, "goal_1")
    assert candidate is not None
    assert candidate.book_id == "ACTION6@30,40"
    assert candidate.action_id == "ACTION6"


def test_to_plan_candidate_non_click_action() -> None:
    """Test that _to_plan_candidate preserves book_id for non-click actions."""
    record = _CandidateRecord(
        action_id="ACTION1",
        book_id="ACTION1",
        payload={},
        score=0.5,
        rationale="test action",
        expected_effect="grid change",
        predicted_outcome={"kind": "grid_change", "confidence": 0.4},
        metadata={"goal_id": "goal_1"},
    )
    candidate = PlanGenerator._to_plan_candidate(record, "goal_1")
    assert candidate is not None
    assert candidate.book_id == "ACTION1"
    assert candidate.action_id == "ACTION1"


def test_plan_candidate_none_handling() -> None:
    """Test that _to_plan_candidate returns None when passed None."""
    result = PlanGenerator._to_plan_candidate(None, "goal_1")
    assert result is None


def _goal() -> ResolvedGoal:
    return ResolvedGoal(
        selected=GoalHypothesis(goal_id="goal-1", description="Goal one", confidence=0.8, metadata={"preferred_actions": ()}),
        metadata={"source": "resolved-goal"},
    )


def _perception(*, available_actions: tuple[str, ...]) -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"available_actions": list(available_actions)},
        grid_hash="grid-1",
        metadata={"available_actions": list(available_actions)},
    )


def test_build_candidates_veto_alternative_reuses_click_target_book_id() -> None:
    """plan_generator.py::_build_candidates's veto-alternative reconstruction
    (both the book_id= constructor arg and metadata["book_id"]) must read the
    already-resolved .book_id field, not re-derive it -- confirm this for a
    click-target veto alternative whose action_id (ACTION6) is shared by many
    coordinates but whose book_id is the specific coordinate."""
    generator = PlanGenerator()
    veto_alternative = PlanCandidate(
        action_id="ACTION6",
        goal_id="goal-1",
        score=0.3,
        rationale="try this coordinate",
        book_id="ACTION6@7,7",
        metadata={"book_id": "ACTION6@7,7"},
    )
    state = WorkflowState(replan_passes=1, latest_veto_alternative=veto_alternative)
    perception = _perception(available_actions=("ACTION1",))

    result = generator.generate(state, perception, _goal(), graph_port=None)

    assert result.payload is not None
    all_candidates = ([result.payload.candidate] if result.payload.candidate else []) + list(result.payload.alternatives)
    veto_reconstructed = [c for c in all_candidates if c.action_id == "ACTION6"]
    assert len(veto_reconstructed) == 1
    assert veto_reconstructed[0].book_id == "ACTION6@7,7"
    assert veto_reconstructed[0].metadata["book_id"] == "ACTION6@7,7"


def test_build_candidates_veto_alternative_non_click_action() -> None:
    """Same reconstruction path for a non-click action, where book_id == action_id."""
    generator = PlanGenerator()
    veto_alternative = PlanCandidate(
        action_id="ACTION3",
        goal_id="goal-1",
        score=0.3,
        rationale="try this action",
        metadata={},
    )
    state = WorkflowState(replan_passes=1, latest_veto_alternative=veto_alternative)
    perception = _perception(available_actions=("ACTION1",))

    result = generator.generate(state, perception, _goal(), graph_port=None)

    assert result.payload is not None
    all_candidates = ([result.payload.candidate] if result.payload.candidate else []) + list(result.payload.alternatives)
    veto_reconstructed = [c for c in all_candidates if c.action_id == "ACTION3"]
    assert len(veto_reconstructed) == 1
    assert veto_reconstructed[0].book_id == "ACTION3"
    assert veto_reconstructed[0].metadata["book_id"] == "ACTION3"
