"""Tests for A184: plan_generator.py's LLM escalation granted any LLM-picked
action_id a flat ~0.3 score floor with no check against the candidate's own
repeated_falsified metadata -- silently overriding the graph's already-correct
falsification verdict. Confirmed live 2026-08-08 (m0r0-492f87ba): an action
falsified twice (deeply negative score) kept getting re-proposed via LLM
guidance, causing a premature second_veto episode termination with 4 untested
actions still available. See backlog/A184.md.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits, _CandidateRecord
from agents.arc4.types import GoalHypothesis, PerceptionSnapshot, ResolvedGoal, WorkflowState


def _repeated_falsified_candidate(score: float = -0.48) -> _CandidateRecord:
    return _CandidateRecord(
        action_id="ACTION4",
        book_id="ACTION4",
        payload={},
        score=score,
        rationale="consider ACTION4 for goal-1; repeatedly falsified x2",
        expected_effect="ACTION4: expect grid_change (p=0.40)",
        predicted_outcome={"kind": "grid_change", "confidence": 0.4},
        metadata={"book_id": "ACTION4", "attempt_count": 2, "falsification_count": 2, "repeated_falsified": True, "untested": False},
    )


def _untested_candidate(action_id: str = "ACTION1", score: float = 0.22) -> _CandidateRecord:
    return _CandidateRecord(
        action_id=action_id,
        book_id=action_id,
        payload={},
        score=score,
        rationale=f"consider {action_id} for goal-1; untested",
        expected_effect=f"{action_id}: expect grid_change (p=0.30)",
        predicted_outcome={"kind": "grid_change", "confidence": 0.3},
        metadata={"book_id": action_id, "attempt_count": 0, "falsification_count": 0, "repeated_falsified": False, "untested": True},
    )


class TestApplyLlmPatchWithheldForRepeatedFalsification:
    def test_repeated_falsified_candidate_score_is_not_overridden(self):
        planner = PlanGenerator(PlanGeneratorLimits())
        candidates = [_repeated_falsified_candidate(score=-0.48)]

        patched = planner._apply_llm_patch(candidates, {"action_id": "ACTION4", "reason": "seems worth another try"})

        assert patched[0].score == -0.48
        assert patched[0].score < PlanGeneratorLimits().replan_feedback_bonus

    def test_llm_guidance_overridden_flag_set(self):
        planner = PlanGenerator(PlanGeneratorLimits())
        candidates = [_repeated_falsified_candidate()]

        patched = planner._apply_llm_patch(candidates, {"action_id": "ACTION4", "reason": "seems worth another try"})

        assert patched[0].metadata.get("llm_guidance_overridden") is True

    def test_llm_reason_still_appended_to_rationale(self):
        """The LLM's input is preserved for transparency/debuggability even
        though its score-floor request is denied -- not a silent no-op."""
        planner = PlanGenerator(PlanGeneratorLimits())
        candidates = [_repeated_falsified_candidate()]

        patched = planner._apply_llm_patch(candidates, {"action_id": "ACTION4", "reason": "seems worth another try"})

        assert "seems worth another try" in patched[0].rationale


class TestApplyLlmPatchUnchangedForNormalCandidates:
    def test_non_repeated_falsified_candidate_still_gets_floor(self):
        """Regression guard: the existing floor-granting behavior for a
        candidate that ISN'T repeatedly falsified must be unchanged."""
        planner = PlanGenerator(PlanGeneratorLimits())
        candidates = [_untested_candidate(score=0.1)]

        patched = planner._apply_llm_patch(candidates, {"action_id": "ACTION1", "reason": "looks promising"})

        assert patched[0].score >= PlanGeneratorLimits().replan_feedback_bonus
        assert "llm_guidance_overridden" not in patched[0].metadata

    def test_unmatched_action_id_fallback_unaffected(self):
        """The LLM picking an action_id outside the candidate list is a
        separate, narrower scenario this card deliberately doesn't touch."""
        planner = PlanGenerator(PlanGeneratorLimits())
        candidates = [_untested_candidate()]

        patched = planner._apply_llm_patch(candidates, {"action_id": "ACTION9", "reason": "hallucinated pick"})

        assert len(patched) == 2
        new_candidate = next(c for c in patched if c.action_id == "ACTION9")
        assert new_candidate.score >= PlanGeneratorLimits().replan_feedback_bonus


class TestEndToEndRepeatedFalsifiedActionLosesToUntested:
    def test_llm_escalation_cannot_promote_falsified_action_over_untested_ones(self):
        """This card's own live reproduction: an action falsified twice must
        not outrank untested alternatives just because the LLM picked it."""

        class _AlwaysPicksFalsifiedActionLLM:
            def chat(self, messages):
                return '{"action_id": "ACTION4", "reason": "seems worth another try"}'

        planner = PlanGenerator(PlanGeneratorLimits())
        state = WorkflowState(
            step_index=2,
            action_attempt_counts={"ACTION4": 2},
            action_falsification_counts={"ACTION4": 2},
            consecutive_no_progress_count=0,
        )
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION1", "ACTION2", "ACTION3", "ACTION4"]},
            grid_hash="hash-1",
        )
        goal = ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="test goal", confidence=0.8))

        result = planner.generate(state, perception, goal, llm_port=_AlwaysPicksFalsifiedActionLLM()).payload

        assert result.candidate.action_id != "ACTION4", (
            f"repeatedly-falsified ACTION4 should not outrank untested alternatives, "
            f"got top candidate {result.candidate.action_id!r} (score={result.candidate.score})"
        )
