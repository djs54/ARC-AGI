"""A150: weak (grid_change) predicted outcomes must falsify, not confirm.

`grid_change` is the weakest possible predicted-outcome kind — nearly any
click in an ARC game changes at least one pixel, so treating a `grid_change`
prediction as "confirmed" even without meaningful progress means
falsification never accumulates for the most common candidate shape
(untested ACTION6 clicks). These tests pin the fix in
`agents/arc4/evaluator.py`: `WEAK_PREDICTION_KINDS`, the
`weak_prediction_override` computation, and the gated
`if grid_changed_flag:` sub-branch.
"""

from __future__ import annotations

from agents.arc4.cycle_policy import record_evaluation_outcome
from agents.arc4.evaluator import Evaluator, EvaluationLimits, WEAK_PREDICTION_KINDS
from agents.arc4.types import (
    ExecutionResult,
    GoalHypothesis,
    PerceptionSnapshot,
    PlanCandidate,
    ResolvedGoal,
    WorkflowDecision,
    WorkflowState,
)


def _goal() -> ResolvedGoal:
    return ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="goal-1", confidence=0.8))


def _perception(*, actions: tuple[str, ...] = ("ACTION1",)) -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"available_actions": list(actions), "grid": [[1]]},
        grid_hash="grid-1",
        metadata={"available_actions": list(actions)},
    )


def _execution(
    *,
    candidate: PlanCandidate,
    did_progress: bool = False,
    metadata: dict | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        action_id=candidate.action_id,
        candidate=candidate,
        observation={"grid": [[1]]},
        did_progress=did_progress,
        predicted_effect=None,
        actual_effect=None,
        metadata=metadata or {},
    )


def test_weak_prediction_kinds_constant():
    assert WEAK_PREDICTION_KINDS == frozenset({"grid_change"})


def test_grid_change_prediction_no_progress_falsifies():
    """ACTION6-style click predicted grid_change, grid did change, no meaningful
    progress: must falsify (falsification_delta=1) instead of confirming."""
    evaluator = Evaluator()
    candidate = PlanCandidate(action_id="ACTION6", predicted_outcome={"kind": "grid_change", "confidence": 0.3})

    result = evaluator.evaluate(
        WorkflowState(),
        _perception(),
        _goal(),
        _execution(candidate=candidate, did_progress=False, metadata={"grid_changed": True, "level_gain": 0}),
    )

    assert result.payload is not None
    assert result.payload.metadata["effect_match"] is False
    assert result.payload.metadata["weak_prediction_override"] is True
    assert result.payload.falsification_delta == 1
    assert result.payload.reason == "weak_prediction_falsified"


def test_grid_change_prediction_with_progress_still_confirms():
    """When did_progress is True, meaningful_progress wins before the weak
    override ever applies — a grid_change prediction that actually helped
    still confirms normally."""
    evaluator = Evaluator()
    candidate = PlanCandidate(action_id="ACTION6", predicted_outcome={"kind": "grid_change", "confidence": 0.3})

    result = evaluator.evaluate(
        WorkflowState(),
        _perception(),
        _goal(),
        _execution(candidate=candidate, did_progress=True, metadata={"grid_changed": True, "level_gain": 0}),
    )

    assert result.payload is not None
    assert result.payload.meaningful_progress is True
    assert result.payload.falsification_delta == 0
    assert result.payload.reason == "meaningful_progress"
    assert result.payload.metadata["weak_prediction_override"] is False


def test_level_gain_prediction_unaffected():
    """A specific (non-weak) predicted kind that turns out wrong already
    falsified before A150 — this must be unchanged."""
    evaluator = Evaluator()
    candidate = PlanCandidate(action_id="ACTION1", predicted_outcome={"kind": "level_gain", "confidence": 0.6})

    result = evaluator.evaluate(
        WorkflowState(),
        _perception(),
        _goal(),
        _execution(candidate=candidate, did_progress=False, metadata={"grid_changed": False, "level_gain": 0}),
    )

    assert result.payload is not None
    assert result.payload.metadata["effect_match"] is False
    assert result.payload.metadata["weak_prediction_override"] is False
    assert result.payload.falsification_delta == 1
    assert result.payload.reason == "prediction_falsified"


def test_state_change_prediction_unaffected():
    """Same regression guard as above, for the state_change kind."""
    evaluator = Evaluator()
    candidate = PlanCandidate(action_id="ACTION1", predicted_outcome={"kind": "state_change", "confidence": 0.6})

    result = evaluator.evaluate(
        WorkflowState(),
        _perception(),
        _goal(),
        _execution(candidate=candidate, did_progress=False, metadata={"grid_changed": False, "level_gain": 0}),
    )

    assert result.payload is not None
    assert result.payload.metadata["effect_match"] is False
    assert result.payload.metadata["weak_prediction_override"] is False
    assert result.payload.falsification_delta == 1
    assert result.payload.reason == "prediction_falsified"


def test_repeated_weak_falsification_reaches_pivot():
    """Two consecutive ACTION6 clicks with grid_change-only outcomes on the
    same action must reach repeated_falsification / PIVOT, matching
    EvaluationLimits.repeated_falsification_threshold=2."""
    evaluator = Evaluator()
    state = WorkflowState()
    candidate = PlanCandidate(action_id="ACTION6", predicted_outcome={"kind": "grid_change", "confidence": 0.3})

    first = evaluator.evaluate(
        state,
        _perception(),
        _goal(),
        _execution(candidate=candidate, did_progress=False, metadata={"grid_changed": True, "level_gain": 0}),
    )
    assert first.payload is not None
    assert first.payload.decision == WorkflowDecision.CONTINUE
    assert first.payload.falsification_delta == 1

    # Mirror what the orchestrator does between steps (workflow.py's
    # _record_evaluation_state -> cycle_policy.record_evaluation_outcome).
    record_evaluation_outcome(
        no_progress_count=state.consecutive_no_progress_count,
        falsification_counts=state.action_falsification_counts,
        action_key="ACTION6",
        meaningful_progress=first.payload.meaningful_progress,
        falsification_delta=first.payload.falsification_delta,
    )

    second = evaluator.evaluate(
        state,
        _perception(),
        _goal(),
        _execution(candidate=candidate, did_progress=False, metadata={"grid_changed": True, "level_gain": 0}),
    )

    assert second.payload is not None
    assert second.payload.decision == WorkflowDecision.PIVOT
    assert second.payload.reason == "repeated_falsification"
    assert second.payload.falsification_delta == 1
    assert second.payload.metadata["projected_falsification_count"] >= EvaluationLimits().repeated_falsification_threshold
