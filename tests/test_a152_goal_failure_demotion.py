"""A152: goal confidence must reflect repeated failure, not just static perception.

Covers:
  1. goal_failure_counts increments on no-progress evaluations.
  2. goal_failure_counts resets to 0 on meaningful progress.
  3. Decay formula is a no-op below the failure threshold.
  4. Decay formula reduces confidence exactly per the documented formula above threshold.
  5. A goal switch occurs once the active goal's decayed confidence drops below an
     alternative's confidence.
  6. Regression guard for the grounding-gate interaction (plan Step 4): a fresh,
     genuinely-better alternative is not suppressed by the grounding gate once the
     active goal has failed and decayed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.goal_resolver import GoalResolver, GoalResolverLimits
from agents.arc4.types import (
    ExecutionResult,
    EvaluationResult,
    GoalHypothesis,
    PerceptionSnapshot,
    ResolvedGoal,
    WorkflowDecision,
    WorkflowState,
)
from agents.arc4.workflow import WorkflowOrchestrator


def _perception(*, grid_hash: str = "grid-1", grid_shape: tuple[int, int] | None = (2, 2)) -> PerceptionSnapshot:
    return PerceptionSnapshot(observation={"grid": grid_hash}, grid_hash=grid_hash, grid_shape=grid_shape)


def _evaluation(*, meaningful_progress: bool) -> EvaluationResult:
    return EvaluationResult(decision=WorkflowDecision.CONTINUE, meaningful_progress=meaningful_progress)


def _execution(action_id: str = "ACTION6") -> ExecutionResult:
    return ExecutionResult(action_id=action_id, candidate=None, observation={})


def test_failure_count_increments_on_no_progress():
    state = WorkflowState(
        active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g1", description="d", confidence=0.57))
    )

    WorkflowOrchestrator._record_evaluation_state(state, _execution(), _evaluation(meaningful_progress=False))
    assert state.goal_failure_counts["g1"] == 1

    WorkflowOrchestrator._record_evaluation_state(state, _execution(), _evaluation(meaningful_progress=False))
    assert state.goal_failure_counts["g1"] == 2


def test_failure_count_resets_on_progress():
    state = WorkflowState(
        active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g1", description="d", confidence=0.57)),
        goal_failure_counts={"g1": 2},
    )

    WorkflowOrchestrator._record_evaluation_state(state, _execution(), _evaluation(meaningful_progress=True))

    assert state.goal_failure_counts["g1"] == 0


def test_decay_formula_below_threshold_noop():
    limits = GoalResolverLimits(goal_failure_threshold=2, goal_failure_decay_factor=0.7)
    resolver = GoalResolver(limits)
    state = WorkflowState(goal_failure_counts={"g1": 1})
    hypothesis = GoalHypothesis(goal_id="g1", description="d", confidence=0.57)

    decayed = resolver._apply_failure_decay(state, [hypothesis])

    assert decayed[0].confidence == 0.57
    assert "failure_decay_applied" not in decayed[0].metadata


def test_decay_formula_above_threshold_reduces_confidence():
    limits = GoalResolverLimits(goal_failure_threshold=2, goal_failure_decay_factor=0.7)
    resolver = GoalResolver(limits)
    state = WorkflowState(goal_failure_counts={"g1": 3})
    hypothesis = GoalHypothesis(goal_id="g1", description="d", confidence=0.57)

    decayed = resolver._apply_failure_decay(state, [hypothesis])

    expected = 0.57 * (0.7 ** (3 - 2 + 1))
    assert decayed[0].confidence == expected
    assert decayed[0].metadata["failure_decay_applied"] is True
    assert decayed[0].metadata["failure_count"] == 3


def test_goal_switches_after_repeated_failure():
    limits = GoalResolverLimits(goal_failure_threshold=2, goal_failure_decay_factor=0.7)
    resolver = GoalResolver(limits)
    state = WorkflowState(goal_failure_counts={"g1": 3})
    hypotheses = [
        GoalHypothesis(goal_id="g1", description="active goal", confidence=0.57),
        GoalHypothesis(goal_id="g2", description="untested alternative", confidence=0.5),
    ]

    decayed = resolver._apply_failure_decay(state, hypotheses)
    ordered = resolver._order_hypotheses(decayed)

    assert ordered[0].goal_id == "g2"
    assert ordered[0].confidence > ordered[1].confidence


def test_grounding_gate_does_not_suppress_fresh_alternative():
    """Regression guard for the plan's Step 4 death-spiral concern.

    Active goal g1 has failed 3 consecutive no-progress cycles and decayed well below
    its own previous-cycle confidence (the grounding-gate ceiling). A fresh alternative
    g2 with genuinely higher confidence than g1's decayed value must not be clamped
    down to g1's stale ceiling -- only g1 itself may be clamped for inflating without
    observed progress.
    """
    limits = GoalResolverLimits(goal_failure_threshold=2, goal_failure_decay_factor=0.7)
    resolver = GoalResolver(limits)

    # Active goal from the previous cycle: g1 at confidence 0.57 (the grounding-gate
    # ceiling for g1 specifically).
    active_goal = ResolvedGoal(
        selected=GoalHypothesis(goal_id="g1", description="active goal", confidence=0.57),
        alternatives=(),
        grounding_gate_passed=True,
    )
    state = WorkflowState(
        previous_grid_hash="grid-1",
        active_goal=active_goal,
        goal_failure_counts={"g1": 3},
    )
    # No grid-hash change -> _observed_progress() is False -> grounding gate engages.
    perception = _perception(grid_hash="grid-1")

    hypotheses = [
        GoalHypothesis(goal_id="g1", description="active goal", confidence=0.57),
        GoalHypothesis(goal_id="g2", description="fresh alternative", confidence=0.65),
    ]

    decayed = resolver._apply_failure_decay(state, hypotheses)
    ordered = resolver._order_hypotheses(decayed)
    gated, grounding_gate_passed = resolver._apply_grounding_gate(state, perception, ordered)
    gated = resolver._order_hypotheses(gated)

    g1 = next(h for h in gated if h.goal_id == "g1")
    g2 = next(h for h in gated if h.goal_id == "g2")

    # g1 was decayed for repeated failure and must not be inflated back up by the gate.
    assert g1.confidence <= 0.57
    # g2 is a fresh alternative untouched by g1's failure history and must retain its
    # own (higher) confidence rather than being clamped down to g1's stale ceiling.
    assert g2.confidence == 0.65
    assert gated[0].goal_id == "g2"

    # End-to-end through resolve(): the selected goal actually switches to g2.
    resolver_full = GoalResolver(limits)

    class _StaticHypothesesResolver(GoalResolver):
        def _tier_one_hypotheses(self, state, perception, *, graph_port=None):  # noqa: D401
            return [
                GoalHypothesis(goal_id="g1", description="active goal", confidence=0.57),
                GoalHypothesis(goal_id="g2", description="fresh alternative", confidence=0.65),
            ]

    end_to_end_resolver = _StaticHypothesesResolver(limits)
    result = end_to_end_resolver.resolve(state, perception)

    assert result.payload is not None
    assert result.payload.selected.goal_id == "g2"
