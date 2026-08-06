"""Tests for A160: check_action_gate reads the server's real `go` field."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.graph_queries import ArcGraphQueryPort
from agents.arc4.plan_vetter import PlanVetter
from agents.arc4.types import PerceptionSnapshot, PlanCandidate, PlanningResult, WorkflowState


class _StubBrainClient:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    def call_tool(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._result


def _port(result: dict[str, Any]) -> ArcGraphQueryPort:
    return ArcGraphQueryPort(_StubBrainClient(result), task_id="task-1", session_id="session-1", strict=False)


class TestCheckActionGateFieldMismatch:
    def test_go_false_produces_allowed_false(self):
        port = _port({"go": False, "reason": "falsified 3 times", "falsification_count": 3, "untested_available": True})
        assert port.check_action_gate("ACTION1")["allowed"] is False

    def test_go_true_produces_allowed_true(self):
        port = _port({"go": True, "reason": "approved"})
        assert port.check_action_gate("ACTION1")["allowed"] is True

    def test_legacy_allowed_key_still_works(self):
        port = _port({"allowed": False})
        assert port.check_action_gate("ACTION1")["allowed"] is False

    def test_capability_missing_defaults_true(self):
        port = _port({"status": "capability_missing"})
        assert port.check_action_gate("ACTION1")["allowed"] is True


class TestPlanVetterVetoesOnGraphGate:
    def test_plan_vetter_vetoes_when_graph_gate_reports_go_false(self):
        port = _port({"go": False, "reason": "graph says no"})
        vetter = PlanVetter(graph_port=port)
        state = WorkflowState(
            step_index=0,
            action_attempt_counts={"ACTION1": 1},
            action_falsification_counts={},
            consecutive_no_progress_count=0,
        )
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION1", "ACTION2"]},
            grid_hash="hash-1",
        )
        goal = None
        plan = PlanningResult(
            candidate=PlanCandidate(action_id="ACTION1", goal_id="goal-1", score=0.5),
            alternatives=(PlanCandidate(action_id="ACTION2", goal_id="goal-1", score=0.4),),
        )

        result = vetter.vet(state, perception, goal, plan)

        assert result.payload.approved is False
        assert result.payload.should_replan is True
        assert result.payload.metadata["veto_type"] == "graph_evidence"
