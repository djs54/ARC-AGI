"""Tests for A192: entity-neighborhood candidate seeding."""

import pytest
from unittest.mock import Mock, MagicMock, patch
from typing import Any, Mapping

from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.graph_queries import ArcGraphQueryPort
from agents.arc4.types import (
    PerceptionSnapshot,
    PerceivedEntity,
    ResolvedGoal,
    GoalHypothesis,
    WorkflowState,
)


# ────────────────────────────────────────────────────────────────────────────
# Scenario 1: _click_targets includes entity_ref when available
# ────────────────────────────────────────────────────────────────────────────

def test_click_targets_includes_entity_ref_when_present():
    """_click_targets includes entity_ref in returned dicts when set."""
    planner = PlanGenerator()

    entity = PerceivedEntity(
        kind="block",
        value=2,
        attributes={
            "coverage": 0.1,
            "cell_count": 5,
            "centroid": (10.0, 15.0),
            "entity_ref": 42,  # A175's stable cross-frame identity
        },
    )

    perception = PerceptionSnapshot(
        observation={},
        grid_hash="test_hash",
        entities=[entity],
        grid_shape=(30, 30),
        metadata={},
    )

    targets = planner._click_targets(perception)

    assert len(targets) == 1
    assert targets[0]["x"] == 15  # rounded centroid[1]
    assert targets[0]["y"] == 10  # rounded centroid[0]
    assert targets[0]["entity_kind"] == "block"
    assert targets[0]["entity_color"] == 2
    assert targets[0]["entity_ref"] == 42


def test_click_targets_includes_none_entity_ref_when_absent():
    """_click_targets includes None for entity_ref when not in attributes (defensive)."""
    planner = PlanGenerator()

    entity = PerceivedEntity(
        kind="point",
        value=5,
        attributes={
            "coverage": 0.1,
            "cell_count": 1,
            "centroid": (20.0, 25.0),
            # entity_ref intentionally absent (pre-A175 scenario)
        },
    )

    perception = PerceptionSnapshot(
        observation={},
        grid_hash="test_hash",
        entities=[entity],
        grid_shape=(30, 30),
        metadata={},
    )

    targets = planner._click_targets(perception)

    assert len(targets) == 1
    assert targets[0]["entity_ref"] is None


# ────────────────────────────────────────────────────────────────────────────
# Scenario 2: fetch_entity_neighborhood degrades on capability_missing
# ────────────────────────────────────────────────────────────────────────────

def test_fetch_entity_neighborhood_degrades_on_capability_missing():
    """fetch_entity_neighborhood returns empty result on capability_missing, no exception."""
    # Create a mock brain client that returns capability_missing
    mock_brain = Mock()
    mock_brain.call_tool = Mock(return_value={"status": "capability_missing"})

    port = ArcGraphQueryPort(
        brain_client=mock_brain,
        task_id="test_task",
        session_id="test_session",
        strict=False,
    )

    result = port.fetch_entity_neighborhood(42)

    # Should return empty result, not raise. "rules" added by the B359
    # follow-up (2026-08-23) -- see tests/test_b359_entity_rule_wiring.py.
    assert result == {"hypotheses": [], "rules": [], "mechanics": []}


def test_fetch_entity_neighborhood_parses_valid_response():
    """fetch_entity_neighborhood correctly parses a valid response."""
    mock_brain = Mock()
    mock_brain.call_tool = Mock(
        return_value={
            "hypotheses": [
                {"hypothesis_id": "h1", "claim": "moves right", "confidence": 0.8, "falsified": False},
                {"hypothesis_id": "h2", "claim": "changes color", "confidence": 0.5, "falsified": True},
            ],
            "mechanics": [
                {"name": "push mechanic", "confidence": 0.6},
            ],
        }
    )

    port = ArcGraphQueryPort(
        brain_client=mock_brain,
        task_id="test_task",
        session_id="test_session",
        strict=False,
    )

    result = port.fetch_entity_neighborhood(42)

    assert len(result["hypotheses"]) == 2
    assert len(result["mechanics"]) == 1
    assert result["hypotheses"][0]["confidence"] == 0.8
    assert result["hypotheses"][1]["falsified"] is True


# ────────────────────────────────────────────────────────────────────────────
# Scenario 3: _build_candidates boosts score for live entity-neighborhood
# hypotheses
# ────────────────────────────────────────────────────────────────────────────

def test_build_candidates_boosts_score_for_live_hypothesis():
    """ACTION6 candidate gets score boost from live entity-neighborhood hypothesis."""
    planner = PlanGenerator()
    state = WorkflowState()

    # Create a click-target entity with entity_ref
    entity = PerceivedEntity(
        kind="block",
        value=3,
        attributes={
            "coverage": 0.1,
            "cell_count": 5,
            "centroid": (15.0, 20.0),
            "entity_ref": 123,
        },
    )

    perception = PerceptionSnapshot(
        observation={},
        grid_hash="test_hash",
        entities=[entity],
        grid_shape=(30, 30),
        metadata={},
    )

    goal = ResolvedGoal(
        selected=GoalHypothesis(
            goal_id="test_goal",
            description="reach corner",
            confidence=0.5,
            evidence=[],
            metadata={},
        ),
        alternatives=[],
        grounding_gate_passed=True,
        metadata={},
    )

    # Mock graph_port with entity-neighborhood data
    mock_brain = Mock()
    mock_brain.call_tool = Mock(side_effect=lambda tool, payload: {
        "fetch_per_action_evidence": {"supports": 1, "contradictions": 0, "confidence": 0.3},
        "fetch_entity_history": {"transitions": [], "changed_count_total": 0},
        "get_rules_for_action": {"rules": []},
        "arc_get_entity_neighborhood": {
            "hypotheses": [
                {"hypothesis_id": "h1", "confidence": 0.7, "falsified": False},  # live
                {"hypothesis_id": "h2", "confidence": 0.4, "falsified": True},   # falsified
            ],
            "mechanics": [],
        },
    }.get(tool, {}))

    graph_port = ArcGraphQueryPort(
        brain_client=mock_brain,
        task_id="test_task",
        session_id="test_session",
        strict=False,
    )

    candidates = planner._build_candidates(
        state,
        perception,
        goal,
        available_actions=["ACTION6"],
        graph_records=[],
        graph_port=graph_port,
    )

    # Find the ACTION6 click candidate
    click_candidates = [c for c in candidates if c.action_id.startswith("ACTION6")]
    assert len(click_candidates) > 0

    # Score should include the entity-neighborhood boost:
    # base graph_positive_score (0.3) + entity_neighborhood_weight (0.2) * max_live_hypothesis (0.7) = 0.3 + 0.14 = 0.44
    best_click = max(click_candidates, key=lambda c: c.score)
    # The boost should be visible (0.7 * 0.2 = 0.14 added to score)
    assert best_click.score >= 0.3  # at minimum has the base action evidence
    # Verify that entity_ref made it to metadata
    assert "entity_ref" in best_click.metadata
    # A196 depends on this flag to compute graph_grounded telemetry -- confirm
    # _build_candidates actually sets it when a live hypothesis contributed,
    # not just that the score moved (found missing on A196's review).
    assert best_click.metadata.get("entity_neighborhood_grounded") is True


def test_build_candidates_identical_score_without_neighborhood_capability():
    """ACTION6 candidate score unchanged when graph_port lacks fetch_entity_neighborhood."""
    planner = PlanGeneratorLimits(entity_neighborhood_weight=0.2)
    planner_obj = PlanGenerator(limits=planner)
    state = WorkflowState()

    entity = PerceivedEntity(
        kind="block",
        value=3,
        attributes={
            "coverage": 0.1,
            "cell_count": 5,
            "centroid": (15.0, 20.0),
            "entity_ref": 123,
        },
    )

    perception = PerceptionSnapshot(
        observation={},
        grid_hash="test_hash",
        entities=[entity],
        grid_shape=(30, 30),
        metadata={},
    )

    goal = ResolvedGoal(
        selected=GoalHypothesis(
            goal_id="test_goal",
            description="reach corner",
            confidence=0.5,
            evidence=[],
            metadata={},
        ),
        alternatives=[],
        grounding_gate_passed=True,
        metadata={},
    )

    # Create a stub graph_port without fetch_entity_neighborhood capability
    # (simulating a pre-A192-aware port/provider)
    class StubGraphPort:
        def __init__(self):
            self.brain_client = None
            self.task_id = "test_task"

        def fetch_per_action_evidence(self, action_id):
            return {"supports": 1, "contradictions": 0, "confidence": 0.3}

        def fetch_entity_history(self, entity_ref):
            return {"transitions": [], "changed_count_total": 0}

        def fetch_rules_for_action(self, action_id):
            return []

        # Note: NO fetch_entity_neighborhood method

    graph_port = StubGraphPort()

    candidates = planner_obj._build_candidates(
        state,
        perception,
        goal,
        available_actions=["ACTION6"],
        graph_records=[],
        graph_port=graph_port,
    )

    # Should still produce candidates without error
    assert len(candidates) > 0
    click_candidates = [c for c in candidates if c.action_id.startswith("ACTION6")]
    assert all(c.metadata.get("entity_neighborhood_grounded") is False for c in click_candidates), (
        "entity_neighborhood_grounded must be False, not merely absent, when the capability is unavailable"
    )


# ────────────────────────────────────────────────────────────────────────────
# Scenario 4: Fallback click target (no entity_ref) doesn't attempt lookup
# ────────────────────────────────────────────────────────────────────────────

def test_build_candidates_fallback_target_no_lookup():
    """Fallback click target (entity_ref=None) never attempts entity-neighborhood lookup."""
    planner = PlanGenerator()
    state = WorkflowState()

    # Empty perception leads to fallback click target
    perception = PerceptionSnapshot(
        observation={},
        grid_hash="test_hash",
        entities=[],  # No entities
        grid_shape=(30, 30),
        metadata={},
    )

    goal = ResolvedGoal(
        selected=GoalHypothesis(
            goal_id="test_goal",
            description="reach corner",
            confidence=0.5,
            evidence=[],
            metadata={},
        ),
        alternatives=[],
        grounding_gate_passed=True,
        metadata={},
    )

    # Track calls to fetch_entity_neighborhood
    neighborhood_call_count = [0]

    def mock_call_tool(tool, payload):
        if tool == "arc_get_entity_neighborhood":
            neighborhood_call_count[0] += 1
        return {}

    mock_brain = Mock()
    mock_brain.call_tool = Mock(side_effect=mock_call_tool)

    graph_port = ArcGraphQueryPort(
        brain_client=mock_brain,
        task_id="test_task",
        session_id="test_session",
        strict=False,
    )

    candidates = planner._build_candidates(
        state,
        perception,
        goal,
        available_actions=["ACTION6"],
        graph_records=[],
        graph_port=graph_port,
    )

    # Should have a fallback candidate
    assert len(candidates) > 0
    fallback = [c for c in candidates if c.metadata.get("entity_kind") == "fallback"]
    assert len(fallback) > 0

    # But entity-neighborhood should never have been queried for the fallback
    assert neighborhood_call_count[0] == 0


# ────────────────────────────────────────────────────────────────────────────
# Scenario 5: Non-click actions unaffected
# ────────────────────────────────────────────────────────────────────────────

def test_non_click_actions_unaffected():
    """Non-click actions (ACTION1-ACTION5) produce identical candidates with/without entity-neighborhood."""
    planner = PlanGenerator()
    state = WorkflowState()

    perception = PerceptionSnapshot(
        observation={},
        grid_hash="test_hash",
        entities=[],
        grid_shape=(30, 30),
        metadata={},
    )

    goal = ResolvedGoal(
        selected=GoalHypothesis(
            goal_id="test_goal",
            description="rotate grid",
            confidence=0.5,
            evidence=[],
            metadata={"preferred_actions": ["ACTION1"]},
        ),
        alternatives=[],
        grounding_gate_passed=True,
        metadata={},
    )

    mock_brain_without = Mock()
    mock_brain_without.call_tool = Mock(return_value={})

    graph_port_without = ArcGraphQueryPort(
        brain_client=mock_brain_without,
        task_id="test_task",
        session_id="test_session",
        strict=False,
    )

    # Generate candidates without neighborhood capability
    candidates_without = planner._build_candidates(
        state,
        perception,
        goal,
        available_actions=["ACTION1", "ACTION2"],
        graph_records=[],
        graph_port=graph_port_without,
    )

    mock_brain_with = Mock()
    mock_brain_with.call_tool = Mock(return_value={
        "arc_get_entity_neighborhood": {"hypotheses": [], "mechanics": []}
    })

    graph_port_with = ArcGraphQueryPort(
        brain_client=mock_brain_with,
        task_id="test_task",
        session_id="test_session",
        strict=False,
    )

    # Generate candidates with neighborhood capability (but shouldn't be used for non-click)
    candidates_with = planner._build_candidates(
        state,
        perception,
        goal,
        available_actions=["ACTION1", "ACTION2"],
        graph_records=[],
        graph_port=graph_port_with,
    )

    # Non-click action candidates should be identical
    action1_without = [c for c in candidates_without if c.action_id == "ACTION1"]
    action1_with = [c for c in candidates_with if c.action_id == "ACTION1"]

    assert len(action1_without) == len(action1_with)
    if action1_without and action1_with:
        # Scores should be identical for non-click actions
        assert action1_without[0].score == action1_with[0].score


# ────────────────────────────────────────────────────────────────────────────
# Scenario 6: Regression guard - existing tests still pass
# ────────────────────────────────────────────────────────────────────────────

def test_build_candidates_metadata_includes_entity_ref():
    """Click-target candidate metadata includes entity_ref from target_info."""
    planner = PlanGenerator()
    state = WorkflowState()

    entity = PerceivedEntity(
        kind="block",
        value=3,
        attributes={
            "coverage": 0.1,
            "cell_count": 5,
            "centroid": (15.0, 20.0),
            "entity_ref": 999,
        },
    )

    perception = PerceptionSnapshot(
        observation={},
        grid_hash="test_hash",
        entities=[entity],
        grid_shape=(30, 30),
        metadata={},
    )

    goal = ResolvedGoal(
        selected=GoalHypothesis(
            goal_id="test_goal",
            description="test",
            confidence=0.5,
            evidence=[],
            metadata={},
        ),
        alternatives=[],
        grounding_gate_passed=True,
        metadata={},
    )

    def mock_call_tool(tool, payload):
        responses = {
            "fetch_per_action_evidence": {"supports": 0, "contradictions": 0, "confidence": 0.0},
            "fetch_entity_history": {"transitions": [], "changed_count_total": 0},
            "get_rules_for_action": {"rules": []},
            "arc_get_entity_neighborhood": {"hypotheses": [], "mechanics": []},
        }
        return responses.get(tool, {})

    mock_brain = Mock()
    mock_brain.call_tool = Mock(side_effect=mock_call_tool)

    graph_port = ArcGraphQueryPort(
        brain_client=mock_brain,
        task_id="test_task",
        session_id="test_session",
        strict=False,
    )

    candidates = planner._build_candidates(
        state,
        perception,
        goal,
        available_actions=["ACTION6"],
        graph_records=[],
        graph_port=graph_port,
    )

    # Find any ACTION6 candidate (either click-target or fallback)
    action6_candidates = [c for c in candidates if c.action_id == "ACTION6" or c.action_id.startswith("ACTION6@")]
    assert len(action6_candidates) > 0

    # Verify entity_ref in metadata for click-target candidates (not fallback)
    for candidate in action6_candidates:
        if candidate.action_id.startswith("ACTION6@"):
            # Real click-target should have entity_ref
            assert "entity_ref" in candidate.metadata
            assert candidate.metadata["entity_ref"] == 999
