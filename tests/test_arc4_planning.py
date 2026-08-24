from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.plan_vetter import PlanVetter
from agents.arc4.types import GoalHypothesis, PerceptionSnapshot, PlanCandidate, PlanningResult, ResolvedGoal, WorkflowState


@dataclass
class RecordingGraphPort:
    payload: object

    def __post_init__(self) -> None:
        self.calls: list[tuple[PerceptionSnapshot, ResolvedGoal | GoalHypothesis | None]] = []

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


def _goal(*, preferred_actions: tuple[str, ...] = (), metadata: dict[str, object] | None = None) -> ResolvedGoal:
    goal_metadata = dict(metadata or {})
    return ResolvedGoal(
        selected=GoalHypothesis(
            goal_id="goal-1",
            description="Goal one",
            confidence=0.8,
            metadata={"preferred_actions": preferred_actions, **goal_metadata},
        ),
        metadata={"source": "resolved-goal"},
    )


def _perception(*, available_actions: tuple[str, ...]) -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"available_actions": list(available_actions)},
        grid_hash="grid-1",
        metadata={"available_actions": list(available_actions)},
    )


def _state(**overrides) -> WorkflowState:
    return WorkflowState(**overrides)


def test_untested_actions_rank_ahead_of_repeatedly_falsified_actions():
    """Updated for A191 (2026-08-23): action-stale has 3 falsifications, at
    or above A191's exclusion threshold (>= 2) -- it's now excluded from
    the candidate set entirely rather than merely out-scored and present
    as a lower-ranked alternative. This is a strictly stronger form of the
    same "untested ranks ahead of falsified" guarantee this test's name
    describes: they don't even compete, since the falsified action was
    never made a candidate at all."""
    generator = PlanGenerator(PlanGeneratorLimits(untested_bonus=0.2, falsification_penalty=0.18, goal_alignment_bonus=0.1))
    perception = _perception(available_actions=("action-stale", "action-fresh"))
    goal = _goal(preferred_actions=("action-stale", "action-fresh"))
    graph = RecordingGraphPort(
        payload=[
            {"action_id": "action-stale", "confidence": 0.45, "rationale": "graph says stale", "expected_effect": "same effect", "goal_id": "goal-1"},
            {"action_id": "action-fresh", "confidence": 0.45, "rationale": "graph says fresh", "expected_effect": "same effect", "goal_id": "goal-1"},
        ]
    )

    state = _state(action_attempt_counts={"action-stale": 4}, action_falsification_counts={"action-stale": 3})
    result = generator.generate(state, perception, goal, graph_port=graph)

    assert len(graph.calls) == 1
    assert result.payload is not None
    assert result.payload.candidate is not None
    assert result.payload.candidate.action_id == "action-fresh"
    assert not any(c.action_id == "action-stale" for c in result.payload.alternatives), (
        "a repeatedly-falsified action must be excluded from the candidate set entirely (A191), "
        "not merely present as a lower-ranked alternative"
    )


def test_goal_conditioning_uses_resolved_goal_contract():
    generator = PlanGenerator()
    perception = _perception(available_actions=("action-goal", "action-other"))
    goal = _goal(preferred_actions=("action-goal",), metadata={"goal_hint": "preferred from contract"})
    graph = RecordingGraphPort(
        payload=[
            {"action_id": "action-goal", "confidence": 0.4, "rationale": "goal-aligned"},
            {"action_id": "action-other", "confidence": 0.4, "rationale": "goal-agnostic"},
        ]
    )

    result = generator.generate(_state(), perception, goal, graph_port=graph)

    assert result.payload is not None
    assert result.payload.candidate is not None
    assert result.payload.candidate.action_id == "action-goal"
    assert result.payload.metadata["goal_contract"]["selected"]["metadata"]["goal_hint"] == "preferred from contract"


def test_vetter_blocks_repeatedly_falsified_action_when_untested_alternative_exists():
    vetter = PlanVetter()
    candidate = PlanCandidate(action_id="action-stale", goal_id="goal-1", score=0.9, rationale="stale")
    alternative = PlanCandidate(action_id="action-fresh", goal_id="goal-1", score=0.6, rationale="fresh")
    plan = PlanningResult(candidate=candidate, alternatives=(alternative,))
    state = _state(action_attempt_counts={"action-stale": 4}, action_falsification_counts={"action-stale": 3})

    result = vetter.vet(state, _perception(available_actions=("action-stale", "action-fresh")), _goal(), plan)

    assert result.status == result.status.VETO
    assert result.payload is not None
    assert not result.payload.approved
    assert result.payload.candidate is candidate
    assert result.payload.alternative is alternative
    assert result.payload.should_replan is True


def test_vetter_allows_action_when_no_better_alternative_exists():
    vetter = PlanVetter()
    candidate = PlanCandidate(action_id="action-stale", goal_id="goal-1", score=0.9, rationale="stale")
    plan = PlanningResult(candidate=candidate, alternatives=())
    state = _state(action_attempt_counts={"action-stale": 4}, action_falsification_counts={"action-stale": 1})

    result = vetter.vet(state, _perception(available_actions=("action-stale",)), _goal(), plan)

    assert result.status == result.status.OK
    assert result.payload is not None
    assert result.payload.approved
    assert result.payload.candidate is candidate
    assert result.payload.alternative is None


def test_veto_feedback_guides_single_replan_pass():
    generator = PlanGenerator()
    perception = _perception(available_actions=("action-stale", "action-fresh"))
    goal = _goal(preferred_actions=("action-stale", "action-fresh"))
    graph = RecordingGraphPort(
        payload=[
            {"action_id": "action-stale", "confidence": 0.62, "rationale": "high confidence"},
            {"action_id": "action-fresh", "confidence": 0.55, "rationale": "slightly lower confidence"},
        ]
    )

    initial_state = _state(action_attempt_counts={"action-stale": 2}, action_falsification_counts={"action-stale": 2})
    first = generator.generate(initial_state, perception, goal, graph_port=graph)
    assert first.payload is not None
    assert first.payload.candidate is not None
    assert first.payload.candidate.action_id == "action-fresh"

    veto_state = _state(
        action_attempt_counts={"action-stale": 2},
        action_falsification_counts={"action-stale": 2},
        latest_veto_alternative=first.payload.candidate,
        replan_passes=1,
    )
    second = generator.generate(veto_state, perception, goal, graph_port=graph)

    assert second.payload is not None
    assert second.payload.candidate is not None
    assert second.payload.candidate.action_id == "action-fresh"
    assert second.payload.metadata["replan_feedback_applied"] is True