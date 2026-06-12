from __future__ import annotations

from agents.arc4.evaluator import Evaluator
from agents.arc4.types import (
    ExecutionResult,
    GoalHypothesis,
    PerceptionSnapshot,
    PlanCandidate,
    ResolvedGoal,
    WorkflowDecision,
    WorkflowState,
)
from run_single_puzzle import _compute_progress


def _goal(goal_id: str = "goal-1") -> ResolvedGoal:
    return ResolvedGoal(selected=GoalHypothesis(goal_id=goal_id, description=goal_id, confidence=0.8))


def _perception() -> PerceptionSnapshot:
    return PerceptionSnapshot(observation={"grid": [[1]]}, grid_hash="grid-1")


def _execution(
    *,
    predicted_effect: str | None = "shift",
    actual_effect: str | None = "rotate",
    did_progress: bool = False,
    metadata: dict | None = None,
) -> ExecutionResult:
    candidate = PlanCandidate(action_id="ACTION1", expected_effect=predicted_effect)
    return ExecutionResult(
        action_id="ACTION1",
        candidate=candidate,
        observation={"grid": [[1]]},
        did_progress=did_progress,
        predicted_effect=predicted_effect,
        actual_effect=actual_effect,
        metadata=metadata or {},
    )


def test_compute_progress_level_gain():
    frame = {"state": "NOT_FINISHED", "levels_completed": 2}
    result = _compute_progress(frame, prev_levels=1, prev_grid_hash="h1", new_grid_hash="h2")
    assert result["did_progress"] is True
    assert result["level_gain"] == 1
    assert result["reward"] == 1.0
    assert result["progress_reward"] == 1.0


def test_compute_progress_win():
    frame = {"state": "WIN", "levels_completed": 0}
    result = _compute_progress(frame, prev_levels=0, prev_grid_hash="h1", new_grid_hash="h1")
    assert result["did_progress"] is True
    assert result["reward"] == 1.0
    assert result["progress_reward"] == 1.0


def test_compute_progress_flat_same_grid():
    frame = {"state": "NOT_FINISHED", "levels_completed": 1}
    result = _compute_progress(frame, prev_levels=1, prev_grid_hash="h1", new_grid_hash="h1")
    assert result["did_progress"] is False
    assert result["grid_changed"] is False
    assert result["level_gain"] == 0


def test_compute_progress_grid_changed_only():
    frame = {"state": "NOT_FINISHED", "levels_completed": 1}
    result = _compute_progress(frame, prev_levels=1, prev_grid_hash="h1", new_grid_hash="h2")
    assert result["did_progress"] is False
    assert result["grid_changed"] is True
    assert result["level_gain"] == 0


def test_evaluator_no_falsification_when_grid_changed():
    evaluator = Evaluator()
    execution = _execution(
        predicted_effect="shift",
        actual_effect="rotate",
        did_progress=False,
        metadata={"grid_changed": True, "level_gain": 0},
    )
    result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), execution)
    assert result.payload is not None
    assert result.payload.falsification_delta == 0
    assert result.payload.reason == "effect_without_progress"


def test_evaluator_falsifies_when_no_effect():
    evaluator = Evaluator()
    execution = _execution(
        predicted_effect="shift",
        actual_effect="rotate",
        did_progress=False,
        metadata={"grid_changed": False, "level_gain": 0},
    )
    result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), execution)
    assert result.payload is not None
    assert result.payload.falsification_delta == 1
    assert result.payload.reason == "prediction_falsified"


def test_evaluator_level_gain_is_meaningful_progress():
    evaluator = Evaluator()
    execution = _execution(
        predicted_effect="shift",
        actual_effect="rotate",
        did_progress=True,
        metadata={"grid_changed": True, "level_gain": 1},
    )
    result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), execution)
    assert result.payload is not None
    assert result.payload.decision == WorkflowDecision.CONTINUE
    assert result.payload.meaningful_progress is True
    assert result.payload.reason == "meaningful_progress"
