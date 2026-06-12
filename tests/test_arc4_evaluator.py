from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.evaluator import EvaluationLimits, Evaluator
from agents.arc4.types import ExecutionResult, GoalHypothesis, PerceptionSnapshot, PlanCandidate, ResolvedGoal, WorkflowDecision, WorkflowState


@dataclass
class RecordingGraphPort:
    def __post_init__(self) -> None:
        self.calls: list[object] = []

    def record_evaluation(self, evaluation):
        self.calls.append(evaluation)
        return {"status": "ok"}


def _goal(goal_id: str = "goal-1") -> ResolvedGoal:
    return ResolvedGoal(selected=GoalHypothesis(goal_id=goal_id, description=goal_id, confidence=0.8))


def _perception() -> PerceptionSnapshot:
    return PerceptionSnapshot(observation={"grid": [[1]]}, grid_hash="grid-1")


def _execution(
    action_id: str = "move-right",
    *,
    predicted_effect: str | None = "shift",
    actual_effect: str | None = "shift",
    did_progress: bool = False,
    metadata: dict | None = None,
) -> ExecutionResult:
    candidate = PlanCandidate(action_id=action_id, expected_effect=predicted_effect)
    return ExecutionResult(
        action_id=action_id,
        candidate=candidate,
        observation={"grid": [[1]]},
        did_progress=did_progress,
        predicted_effect=predicted_effect,
        actual_effect=actual_effect,
        metadata=metadata or {},
    )


def test_prediction_match_path_updates_graph_and_continues():
    graph_port = RecordingGraphPort()
    evaluator = Evaluator(graph_query_port=graph_port)

    result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), _execution(did_progress=True))

    assert result.status.name == "OK"
    assert result.payload is not None
    assert result.payload.decision == WorkflowDecision.CONTINUE
    assert result.payload.meaningful_progress is True
    assert result.payload.falsification_delta == 0
    assert result.payload.metadata["effect_match"] is True
    assert result.payload.metadata["graph_recording"] == "ok"
    assert len(graph_port.calls) == 1


def test_prediction_falsification_sets_delta_without_pivoting_on_first_miss():
    evaluator = Evaluator()

    result = evaluator.evaluate(
        WorkflowState(),
        _perception(),
        _goal(),
        _execution(predicted_effect="shift", actual_effect="rotate", did_progress=False, metadata={"grid_changed": False}),
    )

    assert result.payload is not None
    assert result.payload.decision == WorkflowDecision.CONTINUE
    assert result.payload.meaningful_progress is False
    assert result.payload.falsification_delta == 1
    assert result.payload.metadata["projected_falsification_count"] == 1
    assert result.payload.reason == "prediction_falsified"


def test_repeated_falsification_without_progress_pivots_the_family():
    state = WorkflowState(action_falsification_counts={"move": 1})
    evaluator = Evaluator()

    result = evaluator.evaluate(
        state,
        _perception(),
        _goal(),
        _execution(action_id="move-right", predicted_effect="shift", actual_effect="rotate", did_progress=False, metadata={"grid_changed": False}),
    )

    assert result.payload is not None
    assert result.payload.decision == WorkflowDecision.PIVOT
    assert result.payload.falsification_delta == 1
    assert result.payload.metadata["current_falsification_count"] == 1
    assert result.payload.metadata["projected_falsification_count"] == 2
    assert result.payload.reason == "repeated_falsification"


def test_terminal_game_state_forces_termination():
    evaluator = Evaluator()

    result = evaluator.evaluate(
        WorkflowState(),
        _perception(),
        _goal(),
        _execution(predicted_effect="shift", actual_effect="shift", did_progress=True, metadata={"done": True}),
    )

    assert result.status.name == "TERMINATE"
    assert result.payload is not None
    assert result.payload.decision == WorkflowDecision.TERMINATE
    assert result.payload.reason == "done"
    assert result.payload.metadata["terminal_reason"] == "done"


def test_meaningful_progress_resets_falsification_pressure_in_result_data():
    state = WorkflowState(action_falsification_counts={"move": 2}, action_attempt_counts={"move": 3})
    evaluator = Evaluator(EvaluationLimits(repeated_falsification_threshold=2, exhausted_action_attempt_threshold=4))

    result = evaluator.evaluate(state, _perception(), _goal(), _execution(action_id="move-right", did_progress=True))

    assert result.payload is not None
    assert result.payload.decision == WorkflowDecision.CONTINUE
    assert result.payload.meaningful_progress is True
    assert result.payload.falsification_delta == 0
    assert result.payload.metadata["falsification_pressure_after"] == 0
    assert result.payload.metadata["current_falsification_count"] == 2
    assert result.payload.metadata["current_attempt_count"] == 3