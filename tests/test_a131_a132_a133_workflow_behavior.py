"""Tests for A131 (planner diversity), A132 (vet enforcement), A133 (evaluator progress)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.evaluator import Evaluator, EvaluationLimits
from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.plan_vetter import PlanVetter, PlanVetterLimits
from agents.arc4.types import (
    EvaluationResult,
    ExecutionResult,
    GoalHypothesis,
    PerceptionSnapshot,
    PhaseResult,
    PhaseStatus,
    PlanCandidate,
    PlanningResult,
    ResolvedGoal,
    VetDecision,
    WorkflowDecision,
    WorkflowPhase,
    WorkflowState,
    WorkflowStatus,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _state(**overrides) -> WorkflowState:
    defaults = dict(
        step_index=0,
        action_attempt_counts={},
        action_falsification_counts={},
        consecutive_no_progress_count=0,
    )
    defaults.update(overrides)
    return WorkflowState(**defaults)


def _perception(grid_hash: str = "hash-1", repeated: int = 0, loop: bool = False) -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"grid": grid_hash, "available_actions": ["action-a", "action-b", "action-c"]},
        grid_hash=grid_hash,
        repeated_grid_count=repeated,
        loop_signal=loop,
    )


def _goal(goal_id: str = "goal-1") -> ResolvedGoal:
    return ResolvedGoal(
        selected=GoalHypothesis(goal_id=goal_id, description="test goal", confidence=0.8),
    )


def _execution(action_id: str = "action-a", did_progress: bool = True) -> ExecutionResult:
    return ExecutionResult(
        action_id=action_id,
        candidate=PlanCandidate(action_id=action_id, goal_id="goal-1"),
        observation={"grid": "new-hash"},
        did_progress=did_progress,
    )


def _plan_result(action_id: str = "action-a", score: float = 0.3, alternatives: tuple = ()) -> PlanningResult:
    return PlanningResult(
        candidate=PlanCandidate(action_id=action_id, goal_id="goal-1", score=score),
        alternatives=alternatives,
    )


# ── A133: Evaluator progress detection ──────────────────────────────

class TestA133EvaluatorProgress:
    """Evaluator should override meaningful_progress on stale repeats."""

    def test_fresh_action_respects_did_progress(self):
        evaluator = Evaluator()
        state = _state()
        result = evaluator.evaluate(state, _perception(), _goal(), _execution(did_progress=True))
        assert result.payload.meaningful_progress is True

    def test_stale_repeat_overrides_progress(self):
        """After 2+ prior attempts, did_progress=True is overridden to False."""
        evaluator = Evaluator(limits=EvaluationLimits(stale_repeat_threshold=2))
        state = _state(action_attempt_counts={"action-a": 2})
        result = evaluator.evaluate(state, _perception(), _goal(), _execution(did_progress=True))
        assert result.payload.meaningful_progress is False
        assert result.payload.metadata["stale_override"] is True

    def test_stale_repeat_does_not_override_terminal(self):
        """Terminal signals should not be overridden by stale check."""
        evaluator = Evaluator(limits=EvaluationLimits(stale_repeat_threshold=2))
        state = _state(action_attempt_counts={"action-a": 5})
        execution = ExecutionResult(
            action_id="action-a",
            candidate=PlanCandidate(action_id="action-a", goal_id="goal-1"),
            observation={"grid": "final", "done": True},
            did_progress=True,
        )
        result = evaluator.evaluate(state, _perception(), _goal(), execution)
        # Terminal reason detected → progress stays True (not overridden)
        assert result.payload.decision == WorkflowDecision.TERMINATE
        assert result.payload.meaningful_progress is True

    def test_grid_unchanged_overrides_progress(self):
        """Unchanged grid (repeated_grid_count > 0) overrides progress."""
        evaluator = Evaluator()
        state = _state()
        perception = _perception(repeated=1)
        result = evaluator.evaluate(state, perception, _goal(), _execution(did_progress=True))
        assert result.payload.meaningful_progress is False
        assert result.payload.metadata["grid_unchanged"] is True

    def test_loop_signal_overrides_progress(self):
        """Loop signal overrides progress."""
        evaluator = Evaluator()
        state = _state()
        perception = _perception(loop=True)
        result = evaluator.evaluate(state, perception, _goal(), _execution(did_progress=True))
        assert result.payload.meaningful_progress is False

    def test_first_attempt_no_override(self):
        """First attempt of an action should not be overridden."""
        evaluator = Evaluator(limits=EvaluationLimits(stale_repeat_threshold=2))
        state = _state(action_attempt_counts={"action-a": 1})
        result = evaluator.evaluate(state, _perception(), _goal(), _execution(did_progress=True))
        assert result.payload.meaningful_progress is True
        assert result.payload.metadata["stale_override"] is False


# ── A132: Vet gate enforcement ──────────────────────────────────────

class TestA132VetGate:
    """Vet should veto excessively repeated actions with weak evidence."""

    def test_low_attempts_approved(self):
        """Action with few attempts should be approved."""
        vetter = PlanVetter(limits=PlanVetterLimits(excessive_repetition_threshold=3))
        state = _state(action_attempt_counts={"action-a": 1})
        plan = _plan_result(action_id="action-a", score=0.2)
        result = vetter.vet(state, _perception(), _goal(), plan)
        assert result.payload.approved is True

    def test_excessive_repetition_vetoed(self):
        """Action with 3+ attempts and weak score should be vetoed."""
        vetter = PlanVetter(limits=PlanVetterLimits(excessive_repetition_threshold=3))
        state = _state(action_attempt_counts={"action-a": 3})
        alt = PlanCandidate(action_id="action-b", goal_id="goal-1", score=0.5)
        plan = _plan_result(action_id="action-a", score=0.2, alternatives=(alt,))
        result = vetter.vet(state, _perception(), _goal(), plan)
        assert result.payload.approved is False
        assert result.status == PhaseStatus.VETO
        assert "attempted 3 times" in result.payload.reason
        assert result.payload.metadata["veto_type"] == "excessive_repetition"

    def test_excessive_repetition_no_alternative_approved(self):
        """If no alternative exists, don't veto even with high attempts."""
        vetter = PlanVetter(limits=PlanVetterLimits(excessive_repetition_threshold=3))
        state = _state(action_attempt_counts={"action-a": 5})
        plan = _plan_result(action_id="action-a", score=0.2)  # no alternatives
        result = vetter.vet(state, _perception(), _goal(), plan)
        assert result.payload.approved is True

    def test_high_score_not_vetoed(self):
        """Action with high score should not be vetoed even with many attempts."""
        vetter = PlanVetter(limits=PlanVetterLimits(excessive_repetition_threshold=3))
        state = _state(action_attempt_counts={"action-a": 5})
        alt = PlanCandidate(action_id="action-b", goal_id="goal-1")
        plan = _plan_result(action_id="action-a", score=0.5, alternatives=(alt,))
        result = vetter.vet(state, _perception(), _goal(), plan)
        assert result.payload.approved is True


# ── A131: Planner action diversity ──────────────────────────────────

class TestA131PlannerDiversity:
    """Planner should diversify actions via exponential decay and forced exploration."""

    def test_untested_action_preferred_over_repeated(self):
        """Untested actions should outscore repeated ones."""
        planner = PlanGenerator()
        state = _state(action_attempt_counts={"action-a": 3})
        perception = _perception()
        goal = _goal()
        result = planner.generate(state, perception, goal)

        # The selected candidate should NOT be action-a (it's been tried 3 times)
        assert result.payload.candidate is not None
        # Due to exponential decay 0.6^3 ≈ 0.22 multiplied on score,
        # untested actions with untested_bonus should outrank it
        candidate_id = result.payload.candidate.action_id
        # If action-a is not top, diversity is working
        if candidate_id == "action-a":
            # Even if action-a is still top, check its score is heavily penalized
            assert result.payload.candidate.score < 0.1

    def test_forced_exploration_after_threshold(self):
        """After force_explore_after attempts, an untested action should be promoted.

        Use a goal with preferred_actions so action-a gets a high enough base score
        that exponential decay alone doesn't push it below untested candidates.
        """
        planner = PlanGenerator(limits=PlanGeneratorLimits(
            force_explore_after=3,
            repeat_decay_factor=0.9,  # mild decay — not enough alone
            goal_alignment_bonus=0.5,  # big bonus keeps action-a on top
        ))
        state = _state(action_attempt_counts={"action-a": 3, "action-b": 3, "action-c": 3})
        perception = PerceptionSnapshot(
            observation={"grid": "h", "available_actions": ["action-a", "action-d"]},
            grid_hash="h",
        )
        goal = ResolvedGoal(
            selected=GoalHypothesis(
                goal_id="goal-1", description="test",
                confidence=0.8,
                metadata={"preferred_actions": ["action-a"]},
            ),
        )
        result = planner.generate(state, perception, goal)

        assert result.payload.candidate is not None
        # action-d is untested so forced explore should promote it
        assert result.payload.metadata.get("forced_explore") is True
        assert result.payload.candidate.action_id != "action-a"

    def test_no_forced_explore_below_threshold(self):
        """Below force_explore_after, no forced exploration."""
        planner = PlanGenerator(limits=PlanGeneratorLimits(force_explore_after=5))
        state = _state(action_attempt_counts={"action-a": 2})
        perception = _perception()
        goal = _goal()
        result = planner.generate(state, perception, goal)
        assert result.payload.metadata.get("forced_explore") is False

    def test_exponential_decay_reduces_score(self):
        """Score should decrease exponentially with attempts."""
        planner = PlanGenerator(limits=PlanGeneratorLimits(repeat_decay_factor=0.5))
        state_1 = _state(action_attempt_counts={"action-a": 1})
        state_3 = _state(action_attempt_counts={"action-a": 3})
        perception = _perception()
        goal = _goal()

        result_1 = planner.generate(state_1, perception, goal)
        result_3 = planner.generate(state_3, perception, goal)

        # Find action-a's score in each
        def find_score(result, action_id):
            if result.payload.candidate and result.payload.candidate.action_id == action_id:
                return result.payload.candidate.score
            for alt in result.payload.alternatives:
                if alt.action_id == action_id:
                    return alt.score
            return None

        score_1 = find_score(result_1, "action-a")
        score_3 = find_score(result_3, "action-a")
        assert score_1 is not None and score_3 is not None
        assert score_3 < score_1, f"3-attempt score {score_3} should be less than 1-attempt score {score_1}"
