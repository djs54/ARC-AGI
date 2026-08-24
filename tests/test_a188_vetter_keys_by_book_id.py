"""A188: plan_vetter.py must key attempt/falsification lookups by book_id, not bare action_id."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.plan_vetter import PlanVetter, PlanVetterLimits
from agents.arc4.types import (
    PerceptionSnapshot,
    PlanCandidate,
    PlanningResult,
    GoalHypothesis,
    ResolvedGoal,
    WorkflowState,
)


def _state(**overrides) -> WorkflowState:
    defaults = dict(
        step_index=0,
        action_attempt_counts={},
        action_falsification_counts={},
        consecutive_no_progress_count=0,
    )
    defaults.update(overrides)
    return WorkflowState(**defaults)


def _perception() -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"grid": "hash-1", "available_actions": ["ACTION1", "ACTION6"]},
        grid_hash="hash-1",
    )


def _goal() -> ResolvedGoal:
    return ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="test goal", confidence=0.8))


def _click_candidate(book_id: str, score: float = -1.0) -> PlanCandidate:
    return PlanCandidate(action_id="ACTION6", goal_id="goal-1", score=score, metadata={"book_id": book_id})


class TestA188VetterKeysByBookId:
    def test_vet_reports_real_history_for_candidates_own_book_id(self):
        """This card's exact live reproduction: 6 falsified book_ids on record, candidate is a 7th fresh
        coordinate. vet() must report the *candidate's own* (zero) history, not some other book_id's."""
        state = _state(
            action_attempt_counts={
                "ACTION6@18,17": 5, "ACTION6@36,0": 4, "ACTION6@45,39": 3,
                "ACTION6@10,10": 3, "ACTION6@20,20": 2, "ACTION6@30,30": 2,
            },
            action_falsification_counts={
                "ACTION6@18,17": 3, "ACTION6@36,0": 3, "ACTION6@45,39": 2,
                "ACTION6@10,10": 2, "ACTION6@20,20": 2, "ACTION6@30,30": 1,
            },
        )
        candidate = _click_candidate("ACTION6@50,50", score=0.22)
        plan = PlanningResult(candidate=candidate, alternatives=())
        vetter = PlanVetter(PlanVetterLimits())
        result = vetter.vet(state, _perception(), _goal(), plan)
        assert result.payload.metadata["candidate_attempts"] == 0
        assert result.payload.metadata["candidate_falsifications"] == 0

    def test_vet_reports_real_history_for_already_falsified_book_id(self):
        """A candidate for a coordinate that DOES have recorded history must see its own real numbers."""
        state = _state(
            action_attempt_counts={"ACTION6@18,17": 5, "ACTION6@36,0": 4},
            action_falsification_counts={"ACTION6@18,17": 3, "ACTION6@36,0": 3},
        )
        candidate = _click_candidate("ACTION6@18,17", score=-4.6)
        plan = PlanningResult(candidate=candidate, alternatives=())
        vetter = PlanVetter(PlanVetterLimits())
        result = vetter.vet(state, _perception(), _goal(), plan)
        assert result.payload.metadata["candidate_attempts"] == 5
        assert result.payload.metadata["candidate_falsifications"] == 3

    def test_repeated_falsification_veto_fires_for_click_target(self):
        """Previously could never fire for ACTION6: the lookup always missed and saw 0."""
        state = _state(action_falsification_counts={"ACTION6@18,17": 2})
        candidate = _click_candidate("ACTION6@18,17", score=-1.0)
        alt = PlanCandidate(action_id="ACTION1", goal_id="goal-1", score=0.3)
        plan = PlanningResult(candidate=candidate, alternatives=(alt,))
        vetter = PlanVetter(PlanVetterLimits(repeated_falsification_threshold=2))
        result = vetter.vet(state, _perception(), _goal(), plan)
        assert result.payload.approved is False
        assert result.payload.metadata["veto_type"] == "repeated_falsification"

    def test_excessive_repetition_veto_fires_for_click_target(self):
        """Previously could never fire for ACTION6: the lookup always missed and saw 0 attempts."""
        state = _state(action_attempt_counts={"ACTION6@18,17": 3})
        candidate = _click_candidate("ACTION6@18,17", score=0.1)
        alt = PlanCandidate(action_id="ACTION1", goal_id="goal-1", score=0.3)
        plan = PlanningResult(candidate=candidate, alternatives=(alt,))
        vetter = PlanVetter(PlanVetterLimits(excessive_repetition_threshold=3, weak_evidence_score_threshold=0.4))
        result = vetter.vet(state, _perception(), _goal(), plan)
        assert result.payload.approved is False
        assert result.payload.metadata["veto_type"] == "excessive_repetition"

    def test_choose_alternative_skips_already_attempted_click_target(self):
        """_choose_alternative must not offer an ACTION6 coordinate whose own book_id already has attempts."""
        state = _state(action_attempt_counts={"ACTION6@18,17": 1})
        candidate = PlanCandidate(action_id="ACTION1", goal_id="goal-1", score=-1.0, metadata={"book_id": "ACTION1"})
        already_attempted_alt = _click_candidate("ACTION6@18,17", score=0.2)
        fresh_alt = _click_candidate("ACTION6@99,99", score=0.2)
        plan = PlanningResult(candidate=candidate, alternatives=(already_attempted_alt, fresh_alt))
        vetter = PlanVetter(PlanVetterLimits(excessive_repetition_threshold=1, weak_evidence_score_threshold=0.0))
        result = vetter.vet(state, _perception(), _goal(), plan)
        assert result.payload.alternative is fresh_alt
