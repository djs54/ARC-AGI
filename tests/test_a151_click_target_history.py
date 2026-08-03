"""A151: _click_targets must be aware of previously-attempted ACTION6@x,y coordinates.

Regression coverage for the bug where the planner re-proposed the exact same
click coordinate across steps because target generation was derived purely
from perceived entities, with no reference to `state.action_attempt_counts`.
"""

from __future__ import annotations

from dataclasses import dataclass

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
    kind: str = "point",
    value: str = "2",
    centroid: tuple[float, float],
    cell_count: int = 1,
    coverage: float = 0.01,
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


def test_fresh_coordinate_ranks_first_among_equals():
    # Two entities with identical salience inputs (same kind/value/cell_count/coverage),
    # no attempted_coords -> stable sort preserves insertion order as a sanity baseline.
    e1 = _entity(centroid=(5.0, 5.0))
    e2 = _entity(centroid=(20.0, 20.0))

    targets = PlanGenerator._click_targets(_perception(entities=(e1, e2)), limit=3)

    assert len(targets) == 2
    assert (targets[0]["x"], targets[0]["y"]) == (5, 5)
    assert (targets[1]["x"], targets[1]["y"]) == (20, 20)


def test_exact_repeat_ranks_below_fresh_alternative():
    # Two otherwise-equal entities; one sits at an already-attempted coordinate.
    repeated = _entity(centroid=(5.0, 5.0))  # -> x=5, y=5
    fresh = _entity(centroid=(20.0, 20.0))  # -> x=20, y=20

    targets = PlanGenerator._click_targets(
        _perception(entities=(repeated, fresh)),
        limit=3,
        attempted_coords={(5, 5): 1},
    )

    assert len(targets) == 2
    assert (targets[0]["x"], targets[0]["y"]) == (20, 20)
    assert (targets[1]["x"], targets[1]["y"]) == (5, 5)


def test_all_attempted_still_returns_candidates():
    # Every perceived entity's coordinate has been attempted -> the penalty must not
    # empty the candidate list; it should still return up to `limit`, with the
    # least-attempted coordinate ranked first among the (all negative-salience) repeats.
    less_attempted = _entity(centroid=(5.0, 5.0))  # -> x=5, y=5
    more_attempted = _entity(centroid=(20.0, 20.0))  # -> x=20, y=20

    targets = PlanGenerator._click_targets(
        _perception(entities=(less_attempted, more_attempted)),
        limit=3,
        attempted_coords={(5, 5): 1, (20, 20): 3},
    )

    assert len(targets) == 2
    assert (targets[0]["x"], targets[0]["y"]) == (5, 5)
    assert (targets[1]["x"], targets[1]["y"]) == (20, 20)


def test_unrelated_entities_unaffected():
    # An attempted_coords map that doesn't reference this entity's coordinate must
    # leave its ranking/salience identical to not passing attempted_coords at all.
    entity = _entity(centroid=(10.0, 40.0))  # -> x=40, y=10

    baseline = PlanGenerator._click_targets(_perception(entities=(entity,)), limit=3)
    with_unrelated_history = PlanGenerator._click_targets(
        _perception(entities=(entity,)),
        limit=3,
        attempted_coords={(99, 99): 5},
    )

    assert baseline == with_unrelated_history


def test_build_candidates_threads_state_attempts():
    # Integration: state.action_attempt_counts carries an ACTION6@52,10 history;
    # _build_candidates must thread it through _click_targets so the fresh (10,20)
    # target outranks the twice-attempted (52,10) target after ranking.
    attempted = _entity(kind="point", value="2", centroid=(10.0, 52.0), cell_count=1, coverage=0.01)  # -> x=52, y=10
    fresh = _entity(kind="point", value="9", centroid=(20.0, 10.0), cell_count=1, coverage=0.01)  # -> x=10, y=20
    state = WorkflowState(action_attempt_counts={"ACTION6@52,10": 2})
    generator = PlanGenerator()

    candidates = generator._build_candidates(
        state,
        _perception(entities=(attempted, fresh)),
        _goal(),
        available_actions=("ACTION6",),
        graph_records=[],
        graph_port=GraphPortStub(),
    )
    ranked = generator._rank_candidates(candidates)

    book_ids_in_order = [candidate.book_id for candidate in ranked]
    assert "ACTION6@10,20" in book_ids_in_order
    assert "ACTION6@52,10" in book_ids_in_order
    assert book_ids_in_order.index("ACTION6@10,20") < book_ids_in_order.index("ACTION6@52,10")
