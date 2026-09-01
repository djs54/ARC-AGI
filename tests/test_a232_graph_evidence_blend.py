"""Tests for A232: `plan_generator.py::_build_candidates` already fetched real
Rule-graph evidence via `fetch_rules_for_action` (A177), but only folded it
into the numeric `graph_positive_score` -- never into the `graph_evidence`
dict stored in candidate metadata, which is the ONLY thing
`telemetry.py::_has_positive_graph_evidence`/`_has_graph_evidence_at_all`
(the `graph_grounded`/`graph_informed` KPIs) ever read. Combined with A232's
other fix (removing the `record_reward_prediction_error` call that corrupted
`fetch_per_action_evidence`'s `confidence`/`falsified_count`), this means a
real, working, unfalsified rule was invisible to `graph_grounded` even though
the planner's own score already knew about it.

This blends `fetch_rules_for_action`'s live (unfalsified) rule confidences
into `graph_evidence` itself: `confidence` becomes the max of the two
sources, `supports` gets the live rule count added. See backlog/A232.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.telemetry import _has_positive_graph_evidence
from agents.arc4.types import GoalHypothesis, PerceptionSnapshot, ResolvedGoal, WorkflowState


class MockGraphPort:
    """Scriptable mock exposing both fetch_per_action_evidence (the
    fetch_action_evidence-backed, formerly-corrupted-by-A232's-bug signal)
    and fetch_rules_for_action (the real A177 Rule-graph signal)."""

    def __init__(
        self,
        per_action_evidence: dict[str, dict[str, Any]] | None = None,
        rules_by_action: dict[str, list[dict[str, Any]]] | None = None,
    ):
        self._evidence = per_action_evidence or {}
        self._rules = rules_by_action or {}

    def ingest_perception(self, perception: Any) -> Any:
        return None

    def fetch_goal_evidence(self, perception: Any, goal: Any = None) -> Any:
        return {}

    def record_plan(self, plan: Any) -> Any:
        return None

    def fetch_untested_actions(self) -> list[str]:
        return []

    def fetch_per_action_evidence(self, action_id: str) -> dict[str, Any]:
        return self._evidence.get(action_id, {"supports": 0, "contradictions": 0, "confidence": 0.0, "attempts": 0})

    def check_action_gate(self, action_id: str) -> dict[str, Any]:
        return {"allowed": True, "reason": "no_evidence"}

    def fetch_causal_path(self, action_id: str) -> dict[str, Any]:
        return {"path_exists": False, "supports": False, "contradicts": False}

    def fetch_rules_for_action(self, action_id: str) -> list[dict[str, Any]]:
        return self._rules.get(action_id, [])


def _state(**overrides) -> WorkflowState:
    defaults = dict(
        step_index=0,
        action_attempt_counts={},
        action_falsification_counts={},
        consecutive_no_progress_count=0,
    )
    defaults.update(overrides)
    return WorkflowState(**defaults)


def _perception(grid_hash: str = "hash-1", actions: list[str] | None = None) -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"grid": grid_hash, "available_actions": actions or ["action-a"]},
        grid_hash=grid_hash,
    )


def _goal(goal_id: str = "goal-1") -> ResolvedGoal:
    return ResolvedGoal(
        selected=GoalHypothesis(goal_id=goal_id, description="test goal", confidence=0.8),
    )


def _candidate_for(result, action_id: str):
    candidates = [result.candidate] + list(getattr(result, "alternatives", []) or [])
    for candidate in candidates:
        if candidate is not None and candidate.action_id == action_id:
            return candidate
    return None


class TestGraphEvidenceBlendsRealRuleData:
    def test_corrupted_looking_evidence_is_rescued_by_real_unfalsified_rule(self):
        """A232's exact reported shape: fetch_per_action_evidence looks like
        a fully-falsified action (confidence 0.0, a contradiction, no
        supports) at the same moment fetch_rules_for_action shows a real,
        unfalsified rule. The candidate's graph_evidence must reflect the
        real rule, and _has_positive_graph_evidence must see it as grounded."""
        graph_port = MockGraphPort(
            per_action_evidence={
                "action-a": {"supports": 0, "contradictions": 1, "confidence": 0.0, "attempts": 1},
            },
            rules_by_action={
                "action-a": [
                    {"rule_id": "r1", "confidence": 0.7, "falsified": False},
                ],
            },
        )
        planner = PlanGenerator(PlanGeneratorLimits())
        state = _state()
        perception = _perception(actions=["action-a"])
        goal = _goal()

        result = planner.generate(state, perception, goal, graph_port=graph_port).payload
        candidate = _candidate_for(result, "action-a")

        assert candidate is not None
        graph_evidence = candidate.metadata["graph_evidence"]
        assert graph_evidence["confidence"] >= 0.7
        assert graph_evidence["supports"] >= 1
        assert _has_positive_graph_evidence(graph_evidence) is True

    def test_no_live_rules_leaves_graph_evidence_unchanged(self):
        """Regression: when fetch_rules_for_action returns nothing live,
        graph_evidence must be exactly today's fetch_per_action_evidence-only
        shape -- no accidental mutation when there's nothing real to blend."""
        raw_evidence = {"supports": 2, "contradictions": 1, "confidence": 0.4, "attempts": 3}
        graph_port = MockGraphPort(
            per_action_evidence={"action-a": raw_evidence},
            rules_by_action={"action-a": []},
        )
        planner = PlanGenerator(PlanGeneratorLimits())
        state = _state()
        perception = _perception(actions=["action-a"])
        goal = _goal()

        result = planner.generate(state, perception, goal, graph_port=graph_port).payload
        candidate = _candidate_for(result, "action-a")

        assert candidate is not None
        graph_evidence = candidate.metadata["graph_evidence"]
        assert graph_evidence["confidence"] == raw_evidence["confidence"]
        assert graph_evidence["supports"] == raw_evidence["supports"]
        assert graph_evidence["contradictions"] == raw_evidence["contradictions"]

    def test_all_falsified_rules_leaves_graph_evidence_unchanged(self):
        """A falsified rule must not count as live evidence -- only
        unfalsified rules should ever blend into graph_evidence."""
        raw_evidence = {"supports": 0, "contradictions": 1, "confidence": 0.0, "attempts": 1}
        graph_port = MockGraphPort(
            per_action_evidence={"action-a": raw_evidence},
            rules_by_action={
                "action-a": [{"rule_id": "r1", "confidence": 0.9, "falsified": True}],
            },
        )
        planner = PlanGenerator(PlanGeneratorLimits())
        state = _state()
        perception = _perception(actions=["action-a"])
        goal = _goal()

        result = planner.generate(state, perception, goal, graph_port=graph_port).payload
        candidate = _candidate_for(result, "action-a")

        assert candidate is not None
        graph_evidence = candidate.metadata["graph_evidence"]
        assert graph_evidence["confidence"] == raw_evidence["confidence"]
        assert graph_evidence["supports"] == raw_evidence["supports"]
        assert _has_positive_graph_evidence(graph_evidence) is False
