"""Tests for A208: plan_generator.py::_build_candidates hard-excludes candidates
whose entity_ref has been tested by the graph and found entirely falsified (no live
hypotheses or rules remain), mirroring A191's pattern but at entity-neighborhood
granularity instead of book_id granularity.

A208 is the Shift-C principle (graph as control plane) applied to entity-scoped
evidence: the graph's verdict is authoritative once reached.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.types import GoalHypothesis, PerceivedEntity, PerceptionSnapshot, ResolvedGoal, WorkflowState
from agents.arc4.graph_queries import ArcGraphQueryPort


def _click_entity(
    value: str,
    row: float,
    col: float,
    entity_ref: int | None = None,
    cell_count: int = 5,
) -> PerceivedEntity:
    attributes = {
        "coverage": 0.01,
        "cell_count": cell_count,
        "centroid": (row, col),
    }
    if entity_ref is not None:
        attributes["entity_ref"] = entity_ref
    return PerceivedEntity(kind="point", value=value, attributes=attributes)


def _goal() -> ResolvedGoal:
    return ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="test goal", confidence=0.8))


def _state(**overrides) -> WorkflowState:
    defaults = dict(
        step_index=0,
        action_attempt_counts={},
        action_falsification_counts={},
        consecutive_no_progress_count=0,
    )
    defaults.update(overrides)
    return WorkflowState(**defaults)


def _mock_graph_port(fetch_entity_neighborhood_response: dict[str, Any] | None = None) -> ArcGraphQueryPort:
    """Create a mock graph port with configurable entity-neighborhood response."""
    mock_brain = Mock()

    def mock_call_tool(tool, payload):
        if tool == "arc_get_entity_neighborhood":
            if fetch_entity_neighborhood_response is None:
                return {"hypotheses": [], "rules": [], "mechanics": []}
            return fetch_entity_neighborhood_response
        # Stub other tools
        return {}

    mock_brain.call_tool = Mock(side_effect=mock_call_tool)
    return ArcGraphQueryPort(
        brain_client=mock_brain,
        task_id="test_task",
        session_id="test_session",
        strict=False,
    )


LIMITS = PlanGeneratorLimits()


class TestEntityNeighborhoodHardExclusion:
    """A208: hard exclusion when entity's hypotheses are all falsified."""

    def test_entity_with_only_falsified_hypotheses_excluded(self):
        """Scenario 1: Entity has hypotheses but all are falsified.
        Click candidate targeting this entity is excluded entirely (not just
        scored without a boost)."""
        planner = PlanGenerator(LIMITS)

        entity = _click_entity(value="1", row=18, col=17, entity_ref=123)
        perception = PerceptionSnapshot(
            observation={"available_actions": ["ACTION6"]},
            grid_hash="hash-1",
            entities=[entity],
        )

        # Graph says: this entity has hypotheses, but they're all falsified
        graph_port = _mock_graph_port(
            fetch_entity_neighborhood_response={
                "hypotheses": [
                    {"hypothesis_id": "h1", "confidence": 0.8, "falsified": True},
                    {"hypothesis_id": "h2", "confidence": 0.6, "falsified": True},
                ],
                "rules": [],
                "mechanics": [],
            }
        )

        state = _state()
        candidates = planner._build_candidates(
            state,
            perception,
            _goal(),
            available_actions=["ACTION6"],
            graph_records=[],
            graph_port=graph_port,
        )

        # The ACTION6 candidate targeting entity_ref=123 should be excluded
        action6_candidates = [c for c in candidates if c.action_id == "ACTION6"]
        assert len(action6_candidates) == 0, (
            f"Expected ACTION6 candidate for entity 123 to be excluded "
            f"(all hypotheses falsified), but got {len(action6_candidates)} candidates"
        )
        # A238 (2026-09-01): _build_candidates used to fall back to a
        # synthetic "probe-*" candidate here -- that string was never a
        # real ARC command and guaranteed a 404 at the live transport
        # (backlog/A238.md). It now leaves `candidates` empty instead, so
        # PlanningResult.candidate becomes None rather than a doomed
        # fallback.
        assert candidates == []

    def test_entity_with_only_falsified_rules_excluded(self):
        """Scenario 2: Entity has rules but all are falsified.
        Click candidate is excluded (B359 follow-up: rules are also authoritative
        when all are falsified)."""
        planner = PlanGenerator(LIMITS)

        entity = _click_entity(value="2", row=36, col=0, entity_ref=456)
        perception = PerceptionSnapshot(
            observation={"available_actions": ["ACTION6"]},
            grid_hash="hash-1",
            entities=[entity],
        )

        # Graph says: this entity has rules, but they're all falsified
        graph_port = _mock_graph_port(
            fetch_entity_neighborhood_response={
                "hypotheses": [],
                "rules": [
                    {"rule_id": "r1", "confidence": 0.9, "falsified": True},
                    {"rule_id": "r2", "confidence": 0.7, "falsified": True},
                ],
                "mechanics": [],
            }
        )

        state = _state()
        candidates = planner._build_candidates(
            state,
            perception,
            _goal(),
            available_actions=["ACTION6"],
            graph_records=[],
            graph_port=graph_port,
        )

        # Should be excluded
        action6_candidates = [c for c in candidates if c.action_id == "ACTION6"]
        assert len(action6_candidates) == 0

    def test_entity_with_mixed_hypotheses_not_excluded(self):
        """Scenario 3: Entity has both live and falsified hypotheses.
        Candidate is NOT excluded; the live hypothesis gets a score boost
        (existing A192 behavior)."""
        planner = PlanGenerator(LIMITS)

        entity = _click_entity(value="3", row=45, col=39, entity_ref=789)
        perception = PerceptionSnapshot(
            observation={"available_actions": ["ACTION6"]},
            grid_hash="hash-1",
            entities=[entity],
        )

        # Graph says: this entity has both live and falsified hypotheses
        graph_port = _mock_graph_port(
            fetch_entity_neighborhood_response={
                "hypotheses": [
                    {"hypothesis_id": "h1", "confidence": 0.7, "falsified": False},  # LIVE
                    {"hypothesis_id": "h2", "confidence": 0.4, "falsified": True},   # falsified
                ],
                "rules": [],
                "mechanics": [],
            }
        )

        state = _state()
        candidates = planner._build_candidates(
            state,
            perception,
            _goal(),
            available_actions=["ACTION6"],
            graph_records=[],
            graph_port=graph_port,
        )

        # Candidate should be present (not excluded)
        action6_candidates = [c for c in candidates if c.action_id == "ACTION6"]
        assert len(action6_candidates) > 0, (
            "Entity with live hypothesis should produce a candidate, not be excluded"
        )

        # Score should include the live hypothesis boost
        best = max(action6_candidates, key=lambda c: c.score)
        # Base score 0.0 + live hypothesis boost (0.7 * 0.2 = 0.14) = 0.14 minimum
        assert best.score >= 0.14, (
            f"Live hypothesis should boost score; got {best.score}"
        )
        assert best.metadata.get("entity_neighborhood_grounded") is True

    def test_entity_with_no_neighborhood_record_not_excluded(self):
        """Scenario 4: Entity with no record at all (fresh, ungrounded).
        Candidate is NOT excluded; the graph has said nothing (positive or
        negative) about this entity yet, so exclusion would be premature."""
        planner = PlanGenerator(LIMITS)

        entity = _click_entity(value="4", row=10, col=10, entity_ref=999)
        perception = PerceptionSnapshot(
            observation={"available_actions": ["ACTION6"]},
            grid_hash="hash-1",
            entities=[entity],
        )

        # Graph says: no record for this entity at all
        graph_port = _mock_graph_port(
            fetch_entity_neighborhood_response={
                "hypotheses": [],  # empty
                "rules": [],       # empty
                "mechanics": [],
            }
        )

        state = _state()
        candidates = planner._build_candidates(
            state,
            perception,
            _goal(),
            available_actions=["ACTION6"],
            graph_records=[],
            graph_port=graph_port,
        )

        # Candidate should be present (no record = no exclusion)
        action6_candidates = [c for c in candidates if c.action_id == "ACTION6"]
        assert len(action6_candidates) > 0, (
            "Entity with no neighborhood record should NOT be excluded"
        )

        # entity_neighborhood_grounded should be False (no positive evidence)
        best = max(action6_candidates, key=lambda c: c.score)
        assert best.metadata.get("entity_neighborhood_grounded") is False

    def test_orthogonality_with_a191_fresh_book_id_excluded_by_entity_neighborhood(self):
        """Scenario 5: CRITICAL orthogonality test. A fresh book_id (zero local
        attempts, so A191 would NOT exclude it) whose entity IS neighborhood-
        excluded by A208. Verify the new mechanism is independent from A191."""
        planner = PlanGenerator(LIMITS)

        # This entity has never been clicked locally (fresh this run)
        entity = _click_entity(value="5", row=20, col=20, entity_ref=555)
        perception = PerceptionSnapshot(
            observation={"available_actions": ["ACTION6"]},
            grid_hash="hash-1",
            entities=[entity],
        )

        # Graph says: we've tested this entity before and found it a dead end
        graph_port = _mock_graph_port(
            fetch_entity_neighborhood_response={
                "hypotheses": [
                    {"hypothesis_id": "h1", "confidence": 0.8, "falsified": True},
                ],
                "rules": [],
                "mechanics": [],
            }
        )

        # State: this book_id has ZERO local attempts (A191 would not exclude)
        state = _state(
            action_attempt_counts={"ACTION6@20,20": 0},  # untested locally
            action_falsification_counts={"ACTION6@20,20": 0},  # no local falsifications
        )

        candidates = planner._build_candidates(
            state,
            perception,
            _goal(),
            available_actions=["ACTION6"],
            graph_records=[],
            graph_port=graph_port,
        )

        # A191 would let it through (zero local falsifications < 2)
        # But A208 should exclude it (graph says entity is dead end)
        action6_candidates = [c for c in candidates if c.action_id == "ACTION6"]
        assert len(action6_candidates) == 0, (
            "Fresh book_id targeting a dead-end entity should be excluded by A208 "
            "(graph-level exclusion) even though A191 would not exclude it (local-level)"
        )

        # Verify this is indeed a test of the new mechanism, not A191
        assert (
            state.action_falsification_counts.get("ACTION6@20,20", 0) < 2
        ), "Test setup error: this should be a fresh book_id"

    def test_fetch_entity_neighborhood_exception_no_exclusion(self):
        """Scenario 6: fetch_entity_neighborhood raises an exception.
        Degrades to pre-existing except: pass behavior; candidate is NOT
        excluded (exclusion requires actually observing evidence, not a failed
        fetch)."""
        planner = PlanGenerator(LIMITS)

        entity = _click_entity(value="6", row=30, col=30, entity_ref=666)
        perception = PerceptionSnapshot(
            observation={"available_actions": ["ACTION6"]},
            grid_hash="hash-1",
            entities=[entity],
        )

        # Mock a graph port that raises on fetch_entity_neighborhood
        mock_brain = Mock()

        def mock_call_tool(tool, payload):
            if tool == "arc_get_entity_neighborhood":
                raise RuntimeError("Graph temporarily unavailable")
            return {}

        mock_brain.call_tool = Mock(side_effect=mock_call_tool)
        graph_port = ArcGraphQueryPort(
            brain_client=mock_brain,
            task_id="test_task",
            session_id="test_session",
            strict=False,
        )

        state = _state()
        candidates = planner._build_candidates(
            state,
            perception,
            _goal(),
            available_actions=["ACTION6"],
            graph_records=[],
            graph_port=graph_port,
        )

        # Should produce a candidate despite the exception
        action6_candidates = [c for c in candidates if c.action_id == "ACTION6"]
        assert len(action6_candidates) > 0, (
            "Degradation on exception: candidate should NOT be excluded "
            "just because fetch_entity_neighborhood raised"
        )


class TestRegressionExistingSuites:
    """Scenario 7: Run existing test suites (A191, A192, B359) to confirm they
    still pass unmodified -- A208 must not weaken any prior guarantees."""

    def test_a191_exclusion_still_works(self):
        """A191's repeated_falsified exclusion at book_id level should still work."""
        planner = PlanGenerator(LIMITS)

        entity = _click_entity(value="1", row=18, col=17, entity_ref=111)
        perception = PerceptionSnapshot(
            observation={"available_actions": ["ACTION6"]},
            grid_hash="hash-1",
            entities=[entity],
        )

        # No graph port (A191 doesn't depend on graph)
        state = _state(
            action_falsification_counts={"ACTION6@17,18": 2},  # A191's exclusion trigger
        )

        candidates = planner._build_candidates(
            state,
            perception,
            _goal(),
            available_actions=["ACTION6"],
            graph_records=[],
            graph_port=None,  # A191 works without graph
        )

        # A191's exclusion should still apply
        action6_candidates = [c for c in candidates if c.action_id == "ACTION6"]
        assert len(action6_candidates) == 0, (
            "A191 exclusion (repeated_falsified >= 2) should still work"
        )

    def test_a192_entity_neighborhood_boost_still_works(self):
        """A192's score boost for live entity hypotheses should still work."""
        planner = PlanGenerator(LIMITS)

        entity = _click_entity(value="2", row=36, col=0, entity_ref=222)
        perception = PerceptionSnapshot(
            observation={"available_actions": ["ACTION6"]},
            grid_hash="hash-1",
            entities=[entity],
        )

        # Graph has a live hypothesis (no falsification)
        graph_port = _mock_graph_port(
            fetch_entity_neighborhood_response={
                "hypotheses": [
                    {"hypothesis_id": "h1", "confidence": 0.8, "falsified": False},
                ],
                "rules": [],
                "mechanics": [],
            }
        )

        state = _state()
        candidates = planner._build_candidates(
            state,
            perception,
            _goal(),
            available_actions=["ACTION6"],
            graph_records=[],
            graph_port=graph_port,
        )

        # A192 boost should still apply
        action6_candidates = [c for c in candidates if c.action_id == "ACTION6"]
        assert len(action6_candidates) > 0
        best = max(action6_candidates, key=lambda c: c.score)
        assert best.metadata.get("entity_neighborhood_grounded") is True
        assert best.score >= 0.8 * 0.2, "A192 boost (0.8 * 0.2) should apply"

    def test_b359_entity_rules_boost_still_works(self):
        """B359: score boost for live entity rules should still work independently
        from hypothesis boost."""
        planner = PlanGenerator(LIMITS)

        entity = _click_entity(value="3", row=45, col=39, entity_ref=333)
        perception = PerceptionSnapshot(
            observation={"available_actions": ["ACTION6"]},
            grid_hash="hash-1",
            entities=[entity],
        )

        # Graph has a live rule (no falsification) but no hypotheses
        graph_port = _mock_graph_port(
            fetch_entity_neighborhood_response={
                "hypotheses": [],
                "rules": [
                    {"rule_id": "r1", "confidence": 0.9, "falsified": False},
                ],
                "mechanics": [],
            }
        )

        state = _state()
        candidates = planner._build_candidates(
            state,
            perception,
            _goal(),
            available_actions=["ACTION6"],
            graph_records=[],
            graph_port=graph_port,
        )

        # B359 boost should still apply
        action6_candidates = [c for c in candidates if c.action_id == "ACTION6"]
        assert len(action6_candidates) > 0
        best = max(action6_candidates, key=lambda c: c.score)
        assert best.metadata.get("entity_neighborhood_grounded") is True
        assert best.score >= 0.9 * 0.2, "B359 rule boost (0.9 * 0.2) should apply"

    def test_multiple_entities_mixed_neighborhood_states(self):
        """Integration: multiple entities with different neighborhood states
        (live, falsified, empty) should be handled correctly per-entity."""
        planner = PlanGenerator(LIMITS)

        # Entity 1: has live hypothesis (should be boosted)
        entity1 = _click_entity(value="1", row=10, col=10, entity_ref=101)

        # Entity 2: all falsified (should be excluded)
        entity2 = _click_entity(value="2", row=20, col=20, entity_ref=102)

        # Entity 3: no record (should be neutral, not excluded)
        entity3 = _click_entity(value="3", row=30, col=30, entity_ref=103)

        perception = PerceptionSnapshot(
            observation={"available_actions": ["ACTION6"]},
            grid_hash="hash-1",
            entities=[entity1, entity2, entity3],
        )

        # Mock graph port with per-entity responses
        mock_brain = Mock()

        def mock_call_tool(tool, payload):
            if tool == "arc_get_entity_neighborhood":
                entity_ref = payload.get("entity_ref")
                if entity_ref == 101:
                    return {
                        "hypotheses": [
                            {"hypothesis_id": "h1", "confidence": 0.7, "falsified": False},
                        ],
                        "rules": [],
                        "mechanics": [],
                    }
                elif entity_ref == 102:
                    return {
                        "hypotheses": [
                            {"hypothesis_id": "h2", "confidence": 0.8, "falsified": True},
                        ],
                        "rules": [],
                        "mechanics": [],
                    }
                elif entity_ref == 103:
                    return {
                        "hypotheses": [],
                        "rules": [],
                        "mechanics": [],
                    }
            return {}

        mock_brain.call_tool = Mock(side_effect=mock_call_tool)
        graph_port = ArcGraphQueryPort(
            brain_client=mock_brain,
            task_id="test_task",
            session_id="test_session",
            strict=False,
        )

        state = _state()
        candidates = planner._build_candidates(
            state,
            perception,
            _goal(),
            available_actions=["ACTION6"],
            graph_records=[],
            graph_port=graph_port,
        )

        # Should have 2 ACTION6 candidates (entity1 and entity3, not entity2)
        action6_candidates = [c for c in candidates if c.action_id == "ACTION6"]
        assert len(action6_candidates) == 2, (
            f"Expected 2 ACTION6 candidates (entity 101 live, 102 excluded, 103 empty), "
            f"got {len(action6_candidates)}: {[(c.book_id, c.metadata.get('entity_ref')) for c in action6_candidates]}"
        )

        # Entity 101 should be boosted
        entity1_candidates = [c for c in action6_candidates if c.metadata.get("entity_ref") == 101]
        assert len(entity1_candidates) == 1
        assert entity1_candidates[0].metadata.get("entity_neighborhood_grounded") is True

        # Entity 102 should NOT be in candidates (excluded)
        entity2_candidates = [c for c in action6_candidates if c.metadata.get("entity_ref") == 102]
        assert len(entity2_candidates) == 0, "Entity 102 should be excluded (all falsified)"

        # Entity 103 should be present but not boosted
        entity3_candidates = [c for c in action6_candidates if c.metadata.get("entity_ref") == 103]
        assert len(entity3_candidates) == 1
        assert entity3_candidates[0].metadata.get("entity_neighborhood_grounded") is False
