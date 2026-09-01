"""A171: goal hypotheses ranked by distinctiveness (rare color + small
relative size), not by raster-scan list position.

Regression coverage for `GoalResolver._distinctiveness_score` and the
reworked `_tier_one_hypotheses` ranking pass -- see
backlog/A171.md and backlog/plans/A-171-goal-heuristic-distinctiveness-and-progress-signal.md.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.goal_resolver import GoalResolver
from agents.arc4.types import PerceivedEntity, PerceptionSnapshot, WorkflowState


def _perception(*, grid_hash: str = "grid-1", grid_shape: tuple[int, int] | None = (10, 10), entities: tuple[PerceivedEntity, ...] = ()) -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"grid": grid_hash},
        grid_hash=grid_hash,
        grid_shape=grid_shape,
        entities=entities,
    )


def _state() -> WorkflowState:
    return WorkflowState(previous_grid_hash=None, active_goal=None, consecutive_no_progress_count=0)


@dataclass
class _BoostingGraphPort:
    """Minimal fake graph port that always reports entity history as
    'changed' so _tier_one_hypotheses' history/transfer boosts fire,
    letting confidence climb toward (and get clipped at) the 0.75 cap."""

    def ingest_perception(self, perception):
        return None

    def fetch_goal_evidence(self, perception, goal=None):
        return None

    def record_plan(self, plan):
        return None

    def record_vet(self, vet):
        return None

    def record_execution(self, execution):
        return None

    def record_evaluation(self, evaluation):
        return None

    def fetch_entity_history(self, entity_ref):
        return {
            "changed_count_total": 5,
            "transitions": [{"action_id": "ACTION6", "changed_count": 5}],
        }

    def fetch_transferred_rules(self, fingerprint_key):
        return [
            {"rule_id": "r1", "confidence": 0.9, "source_game_id": "other-game", "fingerprint": fingerprint_key, "preconditions": ()},
            {"rule_id": "r2", "confidence": 0.9, "source_game_id": "other-game", "fingerprint": fingerprint_key, "preconditions": ()},
        ]


def test_rare_small_entity_ranks_first_despite_late_scan_position():
    # Scan order: three large, common-colored ("gray") entities come first,
    # a small, uniquely-colored ("magenta") entity is scanned last. Under
    # the old entities[:3]-by-scan-order logic, the magenta entity could
    # never produce a tier-1 hypothesis at all. Under distinctiveness
    # ranking it should be selected outright.
    entities = (
        PerceivedEntity(kind="block", value="gray", attributes={"coverage": 0.9}),
        PerceivedEntity(kind="block", value="gray", attributes={"coverage": 0.85}),
        PerceivedEntity(kind="block", value="gray", attributes={"coverage": 0.8}),
        PerceivedEntity(kind="point", value="magenta", attributes={"coverage": 0.01}),
    )
    perception = _perception(entities=entities)
    resolver = GoalResolver()

    result = resolver.resolve(_state(), perception)

    assert result.payload is not None
    assert result.payload.selected.goal_id == "point-magenta"
    assert result.payload.metadata["hypotheses"][0]["goal_id"] == "point-magenta"
    # Sanity: raw scan order would have picked only the three gray entities
    # (entities[:3]) and never reached the magenta one.
    raw_scan_pick = [e.value for e in entities[:3]]
    assert "magenta" not in raw_scan_pick


def test_distinctiveness_score_favors_rarity_and_smallness():
    rare_and_small = PerceivedEntity(kind="point", value="red", attributes={"coverage": 0.0})
    common_and_large = PerceivedEntity(kind="block", value="blue", attributes={"coverage": 1.0})

    rare_score = GoalResolver._distinctiveness_score(rare_and_small, {"red": 1})
    common_score = GoalResolver._distinctiveness_score(common_and_large, {"blue": 4})

    assert rare_score == pytest.approx(1.0)
    assert common_score == pytest.approx(0.15)
    assert rare_score > common_score

    # Rarity alone (small color_count) should still beat a common, mid-sized
    # entity even without a coverage advantage.
    common_but_small = PerceivedEntity(kind="point", value="green", attributes={"coverage": 0.0})
    common_small_score = GoalResolver._distinctiveness_score(common_but_small, {"green": 4})
    assert rare_score > common_small_score


def test_confidence_scale_unchanged():
    # Regression guard: even when every boost fires (history + transfer +
    # mechanic fusion), tier-1 confidence must still cap at 0.75 -- other
    # code (grounding gate, etc.) depends on that scale being unchanged by
    # this card's ranking rework.
    entities = (
        PerceivedEntity(kind="point", value="magenta", attributes={"coverage": 0.0, "entity_ref": "e1"}),
    )
    perception = _perception(entities=entities)
    resolver = GoalResolver()

    result = resolver.resolve(_state(), perception, graph_port=_BoostingGraphPort())

    assert result.payload is not None
    assert result.payload.selected.confidence <= 0.75
    assert result.payload.metadata["hypotheses"][0]["confidence"] <= 0.75
