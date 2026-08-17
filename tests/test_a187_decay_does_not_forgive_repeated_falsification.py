"""Tests for A187: plan_generator.py multiplied the *combined* score
(graph_positive_score - graph_contradiction_penalty) by
repeat_decay_factor ** attempts. Applied to a negative value, this shrinks
the falsification penalty toward zero as attempts grow -- confirmed live
2026-08-08 (tu93-0768757b): an action falsified 4 times outscored actions
falsified only once each (-0.2455 vs -0.2680). Fix: only graph_positive_score
is decayed; graph_contradiction_penalty is subtracted afterward at full,
undecayed magnitude. See backlog/A187.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.types import GoalHypothesis, PerceptionSnapshot, ResolvedGoal, WorkflowState

LIMITS = PlanGeneratorLimits()


class _GraphPort:
    def __init__(self, contradictions: int) -> None:
        self._contradictions = contradictions

    def fetch_goal_evidence(self, perception, goal=None):
        return {}

    def fetch_per_action_evidence(self, action_id: str) -> dict[str, Any]:
        return {"supports": 0, "contradictions": self._contradictions, "confidence": 0.0, "attempts": self._contradictions}


def _goal() -> ResolvedGoal:
    return ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="test goal", confidence=0.8))


def _perception(action_id: str = "ACTION1") -> PerceptionSnapshot:
    return PerceptionSnapshot(observation={"grid": "hash-1", "available_actions": [action_id]}, grid_hash="hash-1")


def _score_after_n_falsifications(action_id: str, n: int) -> float:
    """Reproduces this card's live scenario: an action attempted+falsified
    n times, with the graph's own contradiction evidence growing to match
    (mirrors real play, where each real failure adds one more contradiction
    server-side)."""
    graph = _GraphPort(contradictions=n)
    planner = PlanGenerator(LIMITS)
    state = WorkflowState(
        step_index=n,
        action_attempt_counts={action_id: n},
        action_falsification_counts={action_id: n},
        consecutive_no_progress_count=0,
    )
    result = planner.generate(state, _perception(action_id), _goal(), graph_port=graph).payload
    return result.candidate.score


class TestDecayNoLongerForgivesRepeatedFalsification:
    def test_score_does_not_improve_as_attempts_grow(self):
        """Core reproduction: under the old formula, ACTION1's score at
        attempts=2/3/4 was -0.2168/-0.2323/-0.2455 -- moving TOWARD zero
        (improving) as failures accumulated. The fixed formula must not
        improve; each additional real failure should make the score equal
        or worse, never better."""
        scores = [_score_after_n_falsifications("ACTION1", n) for n in (2, 3, 4)]

        assert scores[1] <= scores[0], f"score improved from {scores[0]} to {scores[1]} after a 3rd failure"
        assert scores[2] <= scores[1], f"score improved from {scores[1]} to {scores[2]} after a 4th failure"

    def test_four_failures_scores_worse_than_one_failure(self):
        """This card's exact live finding: an action falsified once must
        outrank an action falsified four times, all else equal."""
        score_one_failure = _score_after_n_falsifications("ACTION1", 1)
        score_four_failures = _score_after_n_falsifications("ACTION1", 4)

        assert score_four_failures < score_one_failure, (
            f"an action falsified 4 times ({score_four_failures}) should score worse than "
            f"one falsified only once ({score_one_failure}), not better"
        )

    def test_exact_live_reproduction_values(self):
        """Recomputes this card's own reverse-engineered trajectory: the
        contradiction penalty grows linearly with attempts (0.16 per unit),
        and under the fix, the decayed score must track that growth
        directly (via the undecayed subtraction), not be swallowed by decay."""
        LIMITS_LOCAL = LIMITS
        for attempts in (2, 3, 4):
            score = _score_after_n_falsifications("ACTION1", attempts)
            decay = LIMITS_LOCAL.repeat_decay_factor**attempts
            graph_contradiction_penalty = LIMITS_LOCAL.falsification_penalty * attempts
            expected = 0.0 * decay - graph_contradiction_penalty - min(LIMITS_LOCAL.repeat_attempt_penalty * attempts, 0.18)
            assert score == pytest.approx(expected), f"attempts={attempts}: got {score}, expected {expected}"


class TestPositiveSignalStillDecays:
    def test_positive_family_evidence_fades_with_repeated_attempts(self):
        """Regression guard: this card must not remove decay entirely --
        a genuinely positive family signal (no contradictions) should still
        fade as the same action is repeated, exactly as before."""

        class _PositiveGraphPort:
            def fetch_goal_evidence(self, perception, goal=None):
                return {}

            def fetch_per_action_evidence(self, action_id: str) -> dict[str, Any]:
                return {"supports": 5, "contradictions": 0, "confidence": 0.6, "attempts": 5}

        planner = PlanGenerator(LIMITS)

        def score_at(attempts: int) -> float:
            state = WorkflowState(
                step_index=attempts,
                action_attempt_counts={"ACTION1": attempts},
                action_falsification_counts={},
                consecutive_no_progress_count=0,
            )
            return planner.generate(state, _perception(), _goal(), graph_port=_PositiveGraphPort()).payload.candidate.score

        score_1 = score_at(1)
        score_3 = score_at(3)
        assert score_3 < score_1, "a positive family signal should still fade (decay) with repeated attempts"


class TestUntestedPathUnaffected:
    def test_first_attempt_scoring_unchanged(self):
        """Regression guard: this card only touches the tested (attempts>0)
        branch -- a genuinely first attempt must be unaffected."""
        graph = _GraphPort(contradictions=0)
        planner = PlanGenerator(LIMITS)
        state = WorkflowState(step_index=0, action_attempt_counts={}, action_falsification_counts={}, consecutive_no_progress_count=0)

        result = planner.generate(state, _perception(), _goal(), graph_port=graph).payload

        assert result.candidate.score == pytest.approx(LIMITS.untested_bonus)
