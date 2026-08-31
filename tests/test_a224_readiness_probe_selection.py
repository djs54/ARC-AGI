"""Tests for A224 Task 4: deterministic probe-selection for the "not ready"
readiness-gate path.

Does NOT route through resolve's LLM escalation or the normal plan_generator/
RETRY machinery, which is tuned for deepening an already-anchored
investigation and would prematurely abandon anchors during broad initial
mapping for the same reason it does today (A224's Problem section, point 5).
Picks the next DISORDER entity each cycle via _click_targets' own existing
salience ordering -- reused, not reinvented, per the plan's explicit
instruction.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.annatar_state_machine import CynefinDomain
from agents.arc4.plan_generator import PlanGenerator
from agents.arc4.types import PerceivedEntity, PerceptionSnapshot


def _entity(entity_ref: int, *, kind: str = "point", value: str = "5", coverage: float = 0.05, cell_count: int = 3, centroid=(10.0, 20.0)) -> PerceivedEntity:
    return PerceivedEntity(
        kind=kind,
        value=value,
        attributes={
            "entity_ref": entity_ref,
            "coverage": coverage,
            "cell_count": cell_count,
            "centroid": centroid,
        },
    )


class TestSelectReadinessProbe:
    def test_picks_a_disorder_entity_ignores_classified_ones(self):
        entities = (_entity(1, centroid=(10.0, 10.0)), _entity(2, centroid=(20.0, 20.0)), _entity(3, centroid=(30.0, 30.0)))
        perception = PerceptionSnapshot(observation={}, grid_hash="h1", entities=entities)
        entity_domains = {1: CynefinDomain.CONVERGED, 2: CynefinDomain.DISORDER, 3: CynefinDomain.COMPLEX}

        planner = PlanGenerator()
        probe = planner._select_readiness_probe(perception, entity_domains)

        assert probe is not None
        assert probe.metadata["entity_ref"] == 2
        assert probe.action_id == "ACTION6"
        assert probe.metadata.get("readiness_probe") is True

    def test_no_disorder_entities_returns_none(self):
        entities = (_entity(1),)
        perception = PerceptionSnapshot(observation={}, grid_hash="h1", entities=entities)
        entity_domains = {1: CynefinDomain.CONVERGED}

        planner = PlanGenerator()
        probe = planner._select_readiness_probe(perception, entity_domains)

        assert probe is None

    def test_multiple_disorder_entities_picks_by_existing_salience_ordering(self):
        """Reuses _click_targets' own salience math (small/rare/point-or-block
        entities score higher) -- not a new priority scheme."""
        small_point = _entity(1, kind="point", cell_count=1, centroid=(5.0, 5.0))
        large_blob = _entity(2, kind="blob", cell_count=50, centroid=(40.0, 40.0), coverage=0.4)
        entities = (large_blob, small_point)
        perception = PerceptionSnapshot(observation={}, grid_hash="h1", entities=entities)
        entity_domains = {1: CynefinDomain.DISORDER, 2: CynefinDomain.DISORDER}

        planner = PlanGenerator()
        probe = planner._select_readiness_probe(perception, entity_domains)

        assert probe is not None
        assert probe.metadata["entity_ref"] == 1, "small point should outrank large blob, same as _click_targets' own salience formula"

    def test_probe_has_no_real_goal_id_sentinel_is_clear(self):
        """No ResolvedGoal exists yet at this point in the cycle by design --
        goal_id must be an unambiguous sentinel, not silently None or a
        fabricated real-looking goal_id."""
        entities = (_entity(1),)
        perception = PerceptionSnapshot(observation={}, grid_hash="h1", entities=entities)
        entity_domains = {1: CynefinDomain.DISORDER}

        planner = PlanGenerator()
        probe = planner._select_readiness_probe(perception, entity_domains)

        assert probe.goal_id == "readiness_probe"
