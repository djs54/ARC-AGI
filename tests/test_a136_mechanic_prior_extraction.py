"""Tests for A136 mechanic prior action_set extraction."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.types import (
    GoalHypothesis,
    PerceptionSnapshot,
    ResolvedGoal,
    WorkflowState,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mechanic_record(action_set: str, confidence: float = 0.65) -> dict:
    return {
        "goal_id": "mechanic_priors",
        "description": "mechanic priors",
        "confidence": confidence,
        "evidence": [],
        "metadata": {
            "source": "mechanic_priors",
            "raw": {
                "mechanics": [
                    {
                        "action_set": action_set,
                        "confidence": confidence,
                        "name": "test mechanic",
                    }
                ]
            },
        },
    }


def _make_goal(goal_id: str = "test-goal") -> ResolvedGoal:
    return ResolvedGoal(
        selected=GoalHypothesis(
            goal_id=goal_id,
            description="test goal",
            confidence=0.8,
            metadata={},
        ),
        alternatives=(),
        grounding_gate_passed=True,
    )


def _make_perception(available_actions: list[str] | None = None) -> PerceptionSnapshot:
    obs = {} if available_actions is None else {"available_actions": available_actions}
    return PerceptionSnapshot(
        observation=obs,
        grid_hash="abc123",
        grid_shape=(2, 2),
        loop_signal=False,
        repeated_grid_count=0,
    )


# ---------------------------------------------------------------------------
# 1. Basic CSV parsing
# ---------------------------------------------------------------------------

def test_extract_mechanic_prior_actions_parses_csv():
    record = _make_mechanic_record("ACTION1,ACTION2,ACTION3")
    result = PlanGenerator._extract_mechanic_prior_actions([record])
    assert result == ["ACTION1", "ACTION2", "ACTION3"]


# ---------------------------------------------------------------------------
# 2. Deduplication across two priors with overlapping action_sets
# ---------------------------------------------------------------------------

def test_extract_mechanic_prior_actions_deduplicates():
    r1 = _make_mechanic_record("ACTION1,ACTION2,ACTION3")
    r2 = _make_mechanic_record("ACTION3,ACTION4,ACTION5")
    result = PlanGenerator._extract_mechanic_prior_actions([r1, r2])
    assert result == ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"]


# ---------------------------------------------------------------------------
# 3. Empty action_set / no mechanics
# ---------------------------------------------------------------------------

def test_extract_mechanic_prior_actions_empty():
    record = _make_mechanic_record("")
    result = PlanGenerator._extract_mechanic_prior_actions([record])
    assert result == []


def test_extract_mechanic_prior_actions_no_priors():
    result = PlanGenerator._extract_mechanic_prior_actions([])
    assert result == []


# ---------------------------------------------------------------------------
# 4. Non-mechanic records are ignored
# ---------------------------------------------------------------------------

def test_extract_mechanic_prior_actions_ignores_non_mechanic_records():
    non_mechanic = {
        "goal_id": "goal-1",
        "description": "some goal",
        "confidence": 0.5,
        "evidence": [],
        "metadata": {
            "source": "graph_evidence",
            "raw": {
                "mechanics": [{"action_set": "ACTION9"}]
            },
        },
    }
    mechanic = _make_mechanic_record("ACTION1,ACTION2")
    result = PlanGenerator._extract_mechanic_prior_actions([non_mechanic, mechanic])
    assert "ACTION9" not in result
    assert result == ["ACTION1", "ACTION2"]


# ---------------------------------------------------------------------------
# 5. End-to-end: planner uses mechanic prior actions as candidates
# ---------------------------------------------------------------------------

def test_planner_uses_mechanic_prior_actions_as_candidates():
    planner = PlanGenerator(PlanGeneratorLimits())
    state = WorkflowState()
    perception = _make_perception()
    goal = _make_goal()

    # graph_port returns mechanic records with action_set
    graph_port = MagicMock()
    graph_port.fetch_goal_evidence.return_value = [
        _make_mechanic_record("ACTION1,ACTION2,ACTION3,ACTION4,ACTION5")
    ]
    graph_port.fetch_untested_actions.return_value = []
    graph_port.fetch_per_action_evidence.return_value = {}

    result = planner.generate(state, perception, goal, graph_port=graph_port)
    plan = result.payload

    total_candidates = (1 if plan.candidate else 0) + len(plan.alternatives)
    # Should have at least the 5 mechanic prior actions
    assert total_candidates >= 5, f"Expected >=5 candidates, got {total_candidates}"

    all_action_ids = [plan.candidate.action_id] if plan.candidate else []
    all_action_ids += [c.action_id for c in plan.alternatives]
    for action in ("ACTION1", "ACTION2", "ACTION3"):
        assert action in all_action_ids, f"{action} not in candidates {all_action_ids}"


# ---------------------------------------------------------------------------
# 6. Candidates from priors have mechanic_prior_source=True
# ---------------------------------------------------------------------------

def test_planner_candidate_metadata_tags_mechanic_source():
    planner = PlanGenerator(PlanGeneratorLimits())
    state = WorkflowState()
    perception = _make_perception()
    goal = _make_goal()

    graph_port = MagicMock()
    graph_port.fetch_goal_evidence.return_value = [
        _make_mechanic_record("ACTION1,ACTION2")
    ]
    graph_port.fetch_untested_actions.return_value = []
    graph_port.fetch_per_action_evidence.return_value = {}

    result = planner.generate(state, perception, goal, graph_port=graph_port)
    plan = result.payload

    all_candidates = ([plan.candidate] if plan.candidate else []) + list(plan.alternatives)
    prior_tagged = [c for c in all_candidates if (c.metadata or {}).get("mechanic_prior_source")]
    prior_action_ids = {c.action_id for c in prior_tagged}
    assert "ACTION1" in prior_action_ids
    assert "ACTION2" in prior_action_ids


# ---------------------------------------------------------------------------
# 7. Deduplication when same action appears in both observation and priors
# ---------------------------------------------------------------------------

def test_planner_deduplicates_mechanic_and_observation_actions():
    planner = PlanGenerator(PlanGeneratorLimits())
    state = WorkflowState()
    # observation already has ACTION1
    perception = _make_perception(available_actions=["ACTION1"])
    goal = _make_goal()

    graph_port = MagicMock()
    # mechanic prior also has ACTION1 plus ACTION2
    graph_port.fetch_goal_evidence.return_value = [
        _make_mechanic_record("ACTION1,ACTION2")
    ]
    graph_port.fetch_untested_actions.return_value = []
    graph_port.fetch_per_action_evidence.return_value = {}

    result = planner.generate(state, perception, goal, graph_port=graph_port)
    plan = result.payload

    all_action_ids = [plan.candidate.action_id] if plan.candidate else []
    all_action_ids += [c.action_id for c in plan.alternatives]

    # ACTION1 must appear exactly once
    assert all_action_ids.count("ACTION1") == 1, (
        f"ACTION1 appears {all_action_ids.count('ACTION1')} times: {all_action_ids}"
    )
    # ACTION2 must appear at least once
    assert "ACTION2" in all_action_ids
