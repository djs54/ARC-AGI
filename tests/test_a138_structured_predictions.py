from __future__ import annotations

from dataclasses import dataclass

from agents.arc4.evaluator import Evaluator
from agents.arc4.plan_generator import PlanGenerator
from agents.arc4.types import ExecutionResult, GoalHypothesis, PerceptionSnapshot, PlanCandidate, ResolvedGoal, WorkflowState


@dataclass
class GraphPortForPlanner:
    records: list[dict]
    action_evidence: dict[str, dict]

    def fetch_goal_evidence(self, perception, goal=None):
        return self.records

    def fetch_per_action_evidence(self, action_id: str):
        return self.action_evidence.get(action_id, {})

    def fetch_untested_actions(self):
        return []


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
    predicted_effect: str | None = "shift",
    actual_effect: str | None = "rotate",
    did_progress: bool = False,
    metadata: dict | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        action_id=candidate.action_id,
        candidate=candidate,
        observation={"grid": [[1]]},
        did_progress=did_progress,
        predicted_effect=predicted_effect,
        actual_effect=actual_effect,
        metadata=metadata or {},
    )


def test_plan_candidate_serialization_round_trip_predicted_outcome():
    candidate = PlanCandidate(
        action_id="ACTION1",
        goal_id="goal-1",
        score=0.7,
        rationale="test",
        expected_effect="expect movement",
        predicted_outcome={"kind": "grid_change", "confidence": 0.7},
    )

    restored = PlanCandidate.from_dict(candidate.to_dict())

    assert restored.predicted_outcome == {"kind": "grid_change", "confidence": 0.7}


def test_untested_action_predicts_grid_change():
    generator = PlanGenerator()
    graph_port = GraphPortForPlanner(records=[], action_evidence={})

    result = generator.generate(WorkflowState(), _perception(actions=("ACTION1",)), _goal(), graph_port=graph_port)

    assert result.payload is not None
    assert result.payload.candidate is not None
    assert result.payload.candidate.predicted_outcome["kind"] == "grid_change"


def test_graph_effect_kind_used_when_present():
    generator = PlanGenerator()
    graph_port = GraphPortForPlanner(
        records=[{"action_id": "ACTION1", "confidence": 0.6}],
        action_evidence={"ACTION1": {"confidence": 0.9, "raw": {"effect_kind": "no_change"}}},
    )

    result = generator.generate(WorkflowState(), _perception(actions=("ACTION1",)), _goal(), graph_port=graph_port)

    assert result.payload is not None
    assert result.payload.candidate is not None
    assert result.payload.candidate.predicted_outcome["kind"] == "no_change"


def test_evaluator_confirms_grid_change_prediction():
    evaluator = Evaluator()
    candidate = PlanCandidate(action_id="ACTION1", predicted_outcome={"kind": "grid_change", "confidence": 0.8})

    result = evaluator.evaluate(
        WorkflowState(),
        _perception(),
        _goal(),
        _execution(candidate=candidate, did_progress=False, metadata={"grid_changed": True, "level_gain": 0}),
    )

    # A150: "grid_change" is the weakest predicted-outcome kind — the raw
    # satisfies_map lookup still matches (observed_kind == "grid_change"), but
    # with no meaningful progress this is now forced to falsify rather than
    # confirm, so the system can learn that this click didn't work. See
    # tests/test_a150_weak_prediction_falsification.py for the full behavior.
    assert result.payload is not None
    assert result.payload.metadata["observed_kind"] == "grid_change"
    assert result.payload.metadata["effect_match"] is False
    assert result.payload.metadata["weak_prediction_override"] is True
    assert result.payload.falsification_delta == 1


def test_evaluator_falsifies_grid_change_prediction_on_no_change():
    evaluator = Evaluator()
    candidate = PlanCandidate(action_id="ACTION1", predicted_outcome={"kind": "grid_change", "confidence": 0.8})

    result = evaluator.evaluate(
        WorkflowState(),
        _perception(),
        _goal(),
        _execution(candidate=candidate, did_progress=False, metadata={"grid_changed": False, "level_gain": 0}),
    )

    assert result.payload is not None
    assert result.payload.metadata["effect_match"] is False
    assert result.payload.falsification_delta == 1


def test_level_gain_satisfies_grid_change_prediction():
    evaluator = Evaluator()
    candidate = PlanCandidate(action_id="ACTION1", predicted_outcome={"kind": "grid_change", "confidence": 0.8})

    result = evaluator.evaluate(
        WorkflowState(),
        _perception(),
        _goal(),
        _execution(candidate=candidate, did_progress=True, metadata={"grid_changed": True, "level_gain": 1}),
    )

    assert result.payload is not None
    assert result.payload.metadata["observed_kind"] == "level_gain"
    assert result.payload.metadata["effect_match"] is True


def test_legacy_string_fallback_when_no_structured_prediction():
    evaluator = Evaluator()
    candidate = PlanCandidate(action_id="ACTION1")

    result = evaluator.evaluate(
        WorkflowState(),
        _perception(),
        _goal(),
        _execution(
            candidate=candidate,
            predicted_effect="shift",
            actual_effect="shift",
            did_progress=False,
            metadata={"grid_changed": False, "level_gain": 0},
        ),
    )

    assert result.payload is not None
    assert result.payload.metadata["predicted_kind"] is None
    assert result.payload.metadata["effect_match"] is True
