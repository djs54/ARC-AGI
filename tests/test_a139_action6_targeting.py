from __future__ import annotations

from dataclasses import dataclass

from agents.arc4.evaluator import Evaluator
from agents.arc4.plan_generator import PlanGenerator
from agents.arc4.types import GoalHypothesis, PerceivedEntity, PerceptionSnapshot, ResolvedGoal, WorkflowState


@dataclass
class GraphPortStub:
    def fetch_goal_evidence(self, perception, goal=None):
        return []

    def fetch_per_action_evidence(self, action_id: str):
        return {}

    def fetch_untested_actions(self):
        return []


def _goal() -> ResolvedGoal:
    return ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="goal-1", confidence=0.8))


def _perception(*, entities: tuple[PerceivedEntity, ...], actions: tuple[str, ...] = ("ACTION6",)) -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"grid": [[1]], "available_actions": list(actions)},
        grid_hash="grid-1",
        entities=entities,
        metadata={"available_actions": list(actions)},
    )


def _entity(
    *,
    kind: str,
    value: str,
    centroid: tuple[float, float],
    cell_count: int,
    coverage: float,
) -> PerceivedEntity:
    return PerceivedEntity(
        kind=kind,
        value=value,
        attributes={
            "centroid": centroid,
            "cell_count": cell_count,
            "coverage": coverage,
        },
    )


def test_click_targets_prefers_small_entities():
    small = _entity(kind="block", value="2", centroid=(10.0, 40.0), cell_count=4, coverage=0.01)
    large = _entity(kind="blob", value="3", centroid=(20.0, 20.0), cell_count=400, coverage=0.2)

    targets = PlanGenerator._click_targets(_perception(entities=(large, small)), limit=3)

    assert targets[0]["x"] == 40
    assert targets[0]["y"] == 10


def test_click_targets_skips_background():
    background = _entity(kind="blob", value="0", centroid=(30.0, 30.0), cell_count=1000, coverage=0.8)
    foreground = _entity(kind="point", value="4", centroid=(5.0, 6.0), cell_count=1, coverage=0.01)

    targets = PlanGenerator._click_targets(_perception(entities=(background, foreground)), limit=3)

    assert len(targets) == 1
    assert targets[0]["x"] == 6
    assert targets[0]["y"] == 5


def test_click_targets_clamps_coordinates():
    entity = _entity(kind="point", value="4", centroid=(70.0, -3.0), cell_count=1, coverage=0.01)

    targets = PlanGenerator._click_targets(_perception(entities=(entity,)), limit=3)

    assert targets[0]["x"] == 0
    assert targets[0]["y"] == 63


def test_click_targets_xy_orientation():
    entity = _entity(kind="point", value="7", centroid=(10.0, 40.0), cell_count=1, coverage=0.01)

    targets = PlanGenerator._click_targets(_perception(entities=(entity,)), limit=3)

    assert targets[0]["x"] == 40
    assert targets[0]["y"] == 10


def test_planner_expands_action6_per_target():
    e1 = _entity(kind="point", value="2", centroid=(5.0, 5.0), cell_count=1, coverage=0.01)
    e2 = _entity(kind="point", value="9", centroid=(20.0, 20.0), cell_count=1, coverage=0.01)
    generator = PlanGenerator()

    result = generator.generate(WorkflowState(), _perception(entities=(e1, e2)), _goal(), graph_port=GraphPortStub())

    assert result.payload is not None
    ranked = result.payload.metadata["ranked_candidates"]
    assert len(ranked) == 2
    payloads = {f"{item['payload']['x']},{item['payload']['y']}" for item in ranked}
    assert payloads == {"5,5", "20,20"}


def test_action6_fallback_center_click_without_entities():
    generator = PlanGenerator()

    result = generator.generate(WorkflowState(), _perception(entities=()), _goal(), graph_port=GraphPortStub())

    assert result.payload is not None
    assert result.payload.candidate is not None
    assert result.payload.candidate.action_id == "ACTION6"
    assert result.payload.candidate.payload == {"x": 32, "y": 32}


def test_book_id_separates_attempt_counts():
    e1 = _entity(kind="point", value="2", centroid=(5.0, 5.0), cell_count=1, coverage=0.01)
    e2 = _entity(kind="point", value="9", centroid=(20.0, 20.0), cell_count=1, coverage=0.01)
    state = WorkflowState(
        action_attempt_counts={"ACTION6@5,5": 2},
        action_falsification_counts={"ACTION6@5,5": 2},
    )
    generator = PlanGenerator()

    result = generator.generate(state, _perception(entities=(e1, e2)), _goal(), graph_port=GraphPortStub())

    assert result.payload is not None
    ranked = result.payload.metadata["ranked_candidates"]
    by_book = {item["book_id"]: item for item in ranked}
    assert by_book["ACTION6@5,5"]["metadata"]["untested"] is False
    assert by_book["ACTION6@20,20"]["metadata"]["untested"] is True
    assert by_book["ACTION6@20,20"]["score"] > by_book["ACTION6@5,5"]["score"]


def test_action_family_of_composite_id():
    assert Evaluator._action_family("ACTION6@10,20") == "ACTION6"
