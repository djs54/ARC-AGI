from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.goal_resolver import GoalResolver, GoalResolverLimits
from agents.arc4.ports import LLMMessage
from agents.arc4.types import GoalHypothesis, PerceivedEntity, PerceptionSnapshot, ResolvedGoal, WorkflowState


@dataclass
class RecordingGraphPort:
    payload: object

    def __post_init__(self) -> None:
        self.calls: list[tuple[PerceptionSnapshot, GoalHypothesis | None]] = []

    def ingest_perception(self, perception):
        return None

    def fetch_goal_evidence(self, perception, goal=None):
        self.calls.append((perception, goal))
        return self.payload

    def record_plan(self, plan):
        return None

    def record_vet(self, vet):
        return None

    def record_execution(self, execution):
        return None

    def record_evaluation(self, evaluation):
        return None


@dataclass
class RecordingLLMPort:
    response: str

    def __post_init__(self) -> None:
        self.calls: list[list[LLMMessage]] = []

    def chat(self, messages):
        self.calls.append(list(messages))
        return self.response


def _perception(*, grid_hash: str = "grid-1", grid_shape: tuple[int, int] | None = (2, 2), entities: tuple[PerceivedEntity, ...] = ()) -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"grid": grid_hash},
        grid_hash=grid_hash,
        grid_shape=grid_shape,
        entities=entities,
    )


def _state(*, previous_grid_hash: str | None = None, active_goal: ResolvedGoal | None = None, consecutive_no_progress_count: int = 0) -> WorkflowState:
    return WorkflowState(
        previous_grid_hash=previous_grid_hash,
        active_goal=active_goal,
        consecutive_no_progress_count=consecutive_no_progress_count,
    )


def test_tier_one_produces_goal_hypothesis_on_step_zero():
    resolver = GoalResolver()
    perception = _perception(entities=(PerceivedEntity(kind="block", value="red", attributes={"count": 1}),))

    result = resolver.resolve(_state(), perception)

    assert result.payload is not None
    assert result.payload.selected.goal_id == "block-red"
    assert result.payload.selected.confidence > 0
    assert result.payload.alternatives == ()
    assert result.payload.metadata["hypotheses"][0]["goal_id"] == "block-red"


def test_graph_evidence_merges_into_the_same_goal_model():
    resolver = GoalResolver()
    perception = _perception(entities=(PerceivedEntity(kind="block", value="red", attributes={}),))
    graph = RecordingGraphPort(
        payload={
            "goal_id": "block-red",
            "description": "Arrange the red block",
            "confidence": 0.84,
            "evidence": ["graph-evidence-1"],
            "metadata": {"source": "graph"},
        }
    )

    result = resolver.resolve(_state(), perception, graph_port=graph)

    assert len(graph.calls) == 1
    assert result.payload is not None
    assert isinstance(result.payload.selected, GoalHypothesis)
    assert result.payload.selected.goal_id == "block-red"
    assert result.payload.selected.description == "Arrange the red block"
    assert result.payload.selected.confidence >= 0.84
    assert "graph-evidence-1" in result.payload.selected.evidence
    assert result.payload.metadata["graph_evidence"][0]["goal_id"] == "block-red"


def test_grounding_gate_blocks_confidence_increase_without_progress():
    resolver = GoalResolver()
    perception = _perception(grid_hash="grid-1", entities=(PerceivedEntity(kind="block", value="red", attributes={}),))
    active_goal = ResolvedGoal(
        selected=GoalHypothesis(goal_id="block-red", description="Arrange the red block", confidence=0.61),
        alternatives=(),
        grounding_gate_passed=True,
    )
    graph = RecordingGraphPort(
        payload={
            "goal_id": "block-red",
            "description": "Arrange the red block",
            "confidence": 0.93,
            "evidence": ["graph-evidence-2"],
        }
    )

    result = resolver.resolve(
        _state(previous_grid_hash="grid-1", active_goal=active_goal, consecutive_no_progress_count=2),
        perception,
        graph_port=graph,
    )

    assert result.payload is not None
    assert result.payload.grounding_gate_passed is False
    assert result.payload.selected.confidence == 0.61
    assert result.payload.selected.goal_id == "block-red"


def test_llm_escalates_only_when_hypotheses_remain_ambiguous():
    resolver = GoalResolver(GoalResolverLimits(ambiguity_gap=0.12, low_confidence_threshold=0.7, llm_patience_steps=2))
    perception = _perception(
        entities=(
            PerceivedEntity(kind="block", value="red", attributes={}),
            PerceivedEntity(kind="block", value="blue", attributes={}),
        )
    )
    llm = RecordingLLMPort(
        response=json.dumps(
            {
                "goal_id": "block-red",
                "description": "Choose the red block goal",
                "confidence": 0.91,
                "reason": "The red block is the active objective.",
            }
        )
    )

    result = resolver.resolve(_state(consecutive_no_progress_count=2), perception, llm_port=llm)

    assert len(llm.calls) == 1
    assert result.payload is not None
    assert result.payload.selected.goal_id == "block-red"
    assert result.payload.selected.confidence == 0.91
    assert result.payload.metadata["llm_escalated"] is True


def test_llm_does_not_escalate_when_hypothesis_is_not_ambiguous():
    resolver = GoalResolver()
    perception = _perception(entities=(PerceivedEntity(kind="block", value="red", attributes={}),))
    llm = RecordingLLMPort(response=json.dumps({"goal_id": "block-red", "confidence": 0.9, "reason": "ignored"}))

    result = resolver.resolve(_state(), perception, llm_port=llm)

    assert len(llm.calls) == 0
    assert result.payload is not None
    assert result.payload.metadata["llm_escalated"] is False