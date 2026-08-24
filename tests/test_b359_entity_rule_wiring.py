"""Tests for the B359 follow-up (2026-08-23): entity-scoped Rule evidence.

hippocampy shipped a new ENTITY_RULE (GridEntity -> Rule) edge, entity_ref
support on record_rule, and a separate "rules" key on
arc_get_entity_neighborhood -- kept apart from "hypotheses"
(ENTITY_HYPOTHESIS) because a confirmed causal rule and a standing
hypothesis under test are different epistemic states. This file covers the
ARC-side half: surfacing entity_ref on record_rule_evidence's payload, and
consuming the new "rules" key in candidate scoring.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from agents.arc4.graph_queries import ArcGraphQueryPort
from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.types import ExecutionResult, GoalHypothesis, PerceptionSnapshot, PlanCandidate, ResolvedGoal, WorkflowState


def _entity(entity_ref: int, bbox: tuple[int, int, int, int]) -> SimpleNamespace:
    return SimpleNamespace(attributes={"entity_ref": entity_ref, "bbox": bbox})


def _execution(action_id: str = "ACTION6") -> ExecutionResult:
    return ExecutionResult(action_id=action_id, candidate=PlanCandidate(action_id=action_id), observation={})


class TestRecordRuleEvidenceEntityRef:
    def test_includes_entity_ref_when_attributable(self):
        mock_brain = Mock()
        mock_brain.call_tool = Mock(return_value={"status": "ok"})
        port = ArcGraphQueryPort(brain_client=mock_brain, task_id="task-1", session_id="session-1", strict=False)

        grid_diff = {"changed_cells": [{"row": 5, "col": 5, "from": 2, "to": 5}], "changed_count": 1, "truncated": False}
        entities = [_entity(entity_ref=7, bbox=(0, 0, 10, 10))]

        port.record_rule_evidence(_execution(), grid_diff, entities)

        tool_name, payload = mock_brain.call_tool.call_args[0]
        assert tool_name == "record_rule"
        assert payload["entity_ref"] == 7

    def test_omits_entity_ref_when_no_entity_attributable(self):
        mock_brain = Mock()
        mock_brain.call_tool = Mock(return_value={"status": "ok"})
        port = ArcGraphQueryPort(brain_client=mock_brain, task_id="task-1", session_id="session-1", strict=False)

        grid_diff = {"changed_cells": [{"row": 5, "col": 5, "from": 2, "to": 5}], "changed_count": 1, "truncated": False}

        port.record_rule_evidence(_execution(), grid_diff, entities=())

        tool_name, payload = mock_brain.call_tool.call_args[0]
        assert tool_name == "record_rule"
        assert "entity_ref" not in payload

    def test_entity_ref_zero_is_included_not_treated_as_falsy(self):
        """entity_ref=0 is a real, common value in this codebase -- must not
        be dropped by a truthy check instead of an `is not None` check."""
        mock_brain = Mock()
        mock_brain.call_tool = Mock(return_value={"status": "ok"})
        port = ArcGraphQueryPort(brain_client=mock_brain, task_id="task-1", session_id="session-1", strict=False)

        grid_diff = {"changed_cells": [{"row": 5, "col": 5, "from": 2, "to": 5}], "changed_count": 1, "truncated": False}
        entities = [_entity(entity_ref=0, bbox=(0, 0, 10, 10))]

        port.record_rule_evidence(_execution(), grid_diff, entities)

        tool_name, payload = mock_brain.call_tool.call_args[0]
        assert payload["entity_ref"] == 0


class TestFetchEntityNeighborhoodRulesKey:
    def test_parses_rules_key(self):
        mock_brain = Mock()
        mock_brain.call_tool = Mock(
            return_value={
                "hypotheses": [],
                "rules": [{"rule_id": "r1", "action_family": "ACTION6", "from_color": 2, "to_color": 5, "confidence": 0.7, "falsified": False}],
                "mechanics": [],
            }
        )
        port = ArcGraphQueryPort(brain_client=mock_brain, task_id="task-1", session_id="session-1", strict=False)

        result = port.fetch_entity_neighborhood(entity_ref=7)

        assert len(result["rules"]) == 1
        assert result["rules"][0]["confidence"] == 0.7

    def test_degrades_to_empty_rules_on_capability_missing(self):
        mock_brain = Mock()
        mock_brain.call_tool = Mock(return_value={"status": "capability_missing"})
        port = ArcGraphQueryPort(brain_client=mock_brain, task_id="task-1", session_id="session-1", strict=False)

        result = port.fetch_entity_neighborhood(entity_ref=7)

        assert result == {"hypotheses": [], "rules": [], "mechanics": []}

    def test_rules_key_absent_from_response_degrades_to_empty_list(self):
        mock_brain = Mock()
        mock_brain.call_tool = Mock(return_value={"hypotheses": [], "mechanics": []})
        port = ArcGraphQueryPort(brain_client=mock_brain, task_id="task-1", session_id="session-1", strict=False)

        result = port.fetch_entity_neighborhood(entity_ref=7)

        assert result["rules"] == []


def _goal() -> ResolvedGoal:
    return ResolvedGoal(
        selected=GoalHypothesis(goal_id="goal-1", description="test goal", confidence=0.8, metadata={"preferred_actions": ()}),
        metadata={},
    )


def _perception() -> PerceptionSnapshot:
    from agents.arc4.types import PerceivedEntity

    entity = PerceivedEntity(
        kind="block",
        value=3,
        attributes={"coverage": 0.1, "cell_count": 5, "centroid": (15.0, 20.0), "entity_ref": 7},
    )
    return PerceptionSnapshot(observation={}, grid_hash="hash-1", entities=[entity], grid_shape=(30, 30), metadata={})


class TestBuildCandidatesConsumesRules:
    def test_live_rule_boosts_score_and_sets_grounded_flag(self):
        planner = PlanGenerator(PlanGeneratorLimits(entity_rule_weight=0.2))
        mock_brain = Mock()
        mock_brain.call_tool = Mock(
            side_effect=lambda tool, payload: {
                "fetch_per_action_evidence": {"supports": 0, "contradictions": 0, "confidence": 0.0},
                "fetch_entity_history": {"transitions": [], "changed_count_total": 0},
                "get_rules_for_action": {"rules": []},
                "arc_get_entity_neighborhood": {
                    "hypotheses": [],
                    "rules": [{"rule_id": "r1", "confidence": 0.6, "falsified": False}],
                    "mechanics": [],
                },
            }.get(tool, {})
        )
        graph_port = ArcGraphQueryPort(brain_client=mock_brain, task_id="task-1", session_id="session-1", strict=False)

        candidates = planner._build_candidates(
            WorkflowState(), _perception(), _goal(), available_actions=["ACTION6"], graph_records=[], graph_port=graph_port
        )

        click_candidates = [c for c in candidates if c.action_id.startswith("ACTION6")]
        assert len(click_candidates) > 0
        best = max(click_candidates, key=lambda c: c.score)
        assert best.score >= 0.6 * 0.2
        assert best.metadata.get("entity_neighborhood_grounded") is True

    def test_falsified_rule_does_not_boost_score(self):
        planner = PlanGenerator(PlanGeneratorLimits(entity_rule_weight=0.2, entity_neighborhood_weight=0.2))
        mock_brain = Mock()
        mock_brain.call_tool = Mock(
            side_effect=lambda tool, payload: {
                "fetch_per_action_evidence": {"supports": 0, "contradictions": 0, "confidence": 0.0},
                "fetch_entity_history": {"transitions": [], "changed_count_total": 0},
                "get_rules_for_action": {"rules": []},
                "arc_get_entity_neighborhood": {
                    "hypotheses": [],
                    "rules": [{"rule_id": "r1", "confidence": 0.9, "falsified": True}],
                    "mechanics": [],
                },
            }.get(tool, {})
        )
        graph_port = ArcGraphQueryPort(brain_client=mock_brain, task_id="task-1", session_id="session-1", strict=False)

        candidates = planner._build_candidates(
            WorkflowState(), _perception(), _goal(), available_actions=["ACTION6"], graph_records=[], graph_port=graph_port
        )

        click_candidates = [c for c in candidates if c.action_id.startswith("ACTION6")]
        assert all(c.metadata.get("entity_neighborhood_grounded") is False for c in click_candidates)

    def test_hypothesis_and_rule_boosts_combine_additively(self):
        planner = PlanGenerator(PlanGeneratorLimits(entity_rule_weight=0.2, entity_neighborhood_weight=0.2))
        mock_brain = Mock()
        mock_brain.call_tool = Mock(
            side_effect=lambda tool, payload: {
                "fetch_per_action_evidence": {"supports": 0, "contradictions": 0, "confidence": 0.0},
                "fetch_entity_history": {"transitions": [], "changed_count_total": 0},
                "get_rules_for_action": {"rules": []},
                "arc_get_entity_neighborhood": {
                    "hypotheses": [{"hypothesis_id": "h1", "confidence": 0.5, "falsified": False}],
                    "rules": [{"rule_id": "r1", "confidence": 0.5, "falsified": False}],
                    "mechanics": [],
                },
            }.get(tool, {})
        )
        graph_port = ArcGraphQueryPort(brain_client=mock_brain, task_id="task-1", session_id="session-1", strict=False)

        combined = planner._build_candidates(
            WorkflowState(), _perception(), _goal(), available_actions=["ACTION6"], graph_records=[], graph_port=graph_port
        )
        combined_score = max(c.score for c in combined if c.action_id.startswith("ACTION6"))

        # Same setup but rules empty -- isolate the hypothesis-only contribution.
        mock_brain_hyp_only = Mock()
        mock_brain_hyp_only.call_tool = Mock(
            side_effect=lambda tool, payload: {
                "fetch_per_action_evidence": {"supports": 0, "contradictions": 0, "confidence": 0.0},
                "fetch_entity_history": {"transitions": [], "changed_count_total": 0},
                "get_rules_for_action": {"rules": []},
                "arc_get_entity_neighborhood": {
                    "hypotheses": [{"hypothesis_id": "h1", "confidence": 0.5, "falsified": False}],
                    "rules": [],
                    "mechanics": [],
                },
            }.get(tool, {})
        )
        graph_port_hyp_only = ArcGraphQueryPort(brain_client=mock_brain_hyp_only, task_id="task-1", session_id="session-1", strict=False)
        hyp_only = planner._build_candidates(
            WorkflowState(), _perception(), _goal(), available_actions=["ACTION6"], graph_records=[], graph_port=graph_port_hyp_only
        )
        hyp_only_score = max(c.score for c in hyp_only if c.action_id.startswith("ACTION6"))

        assert combined_score > hyp_only_score, "rule contribution must add on top of the hypothesis contribution, not replace it"
