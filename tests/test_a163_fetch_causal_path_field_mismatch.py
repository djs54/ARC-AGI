"""Tests for A163: causal-override uses the server's real aggregate path_confidence,
since supports/contradicts itemized lists are never populated by the real server."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.evaluator import EvaluationLimits, Evaluator
from agents.arc4.graph_queries import ArcGraphQueryPort
from agents.arc4.types import ExecutionResult, GoalHypothesis, PerceptionSnapshot, PlanCandidate, ResolvedGoal, WorkflowState


class _StubBrainClient:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    def call_tool(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._result


def _port(result: dict[str, Any]) -> ArcGraphQueryPort:
    return ArcGraphQueryPort(_StubBrainClient(result), task_id="task-1", session_id="session-1", strict=False)


class TestFetchCausalPathFieldMismatch:
    def test_real_server_shape_has_no_itemized_lists(self):
        """Regression: the server genuinely doesn't provide supports/contradicts — don't fabricate data."""
        port = _port({"path_exists": True, "path_length": 2, "path_confidence": 0.6})
        result = port.fetch_causal_path("ACTION1")
        assert result["supports"] == []
        assert result["contradicts"] == []
        assert result["path_confidence"] == 0.6
        assert result["path_exists"] is True

    def test_path_confidence_defaults_to_zero_when_absent(self):
        port = _port({"path_exists": True})
        assert port.fetch_causal_path("ACTION1")["path_confidence"] == 0.0

    def test_capability_missing_returns_no_path(self):
        port = _port({"status": "capability_missing"})
        result = port.fetch_causal_path("ACTION1")
        assert result["path_exists"] is False


def _state() -> WorkflowState:
    return WorkflowState(
        step_index=0,
        action_attempt_counts={},
        action_falsification_counts={},
        consecutive_no_progress_count=0,
    )


def _perception() -> PerceptionSnapshot:
    return PerceptionSnapshot(observation={"grid": "hash-1"}, grid_hash="hash-1")


def _goal() -> ResolvedGoal:
    return ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="test goal", confidence=0.8))


def _execution(action_id: str = "ACTION1") -> ExecutionResult:
    return ExecutionResult(
        action_id=action_id,
        candidate=PlanCandidate(action_id=action_id, goal_id="goal-1"),
        observation={"grid": "new-hash"},
        did_progress=True,
    )


class _CausalPathGraphPort:
    def __init__(self, causal_path: dict[str, Any]) -> None:
        self._causal_path = causal_path

    def fetch_causal_path(self, action_id: str) -> dict[str, Any]:
        return self._causal_path


class TestCausalOverrideConfidenceThreshold:
    def test_low_confidence_with_path_triggers_override(self):
        graph = _CausalPathGraphPort({"path_exists": True, "path_confidence": 0.1})
        evaluator = Evaluator(graph_query_port=graph, limits=EvaluationLimits(causal_override_confidence_threshold=0.3))

        result = evaluator.evaluate(_state(), _perception(), _goal(), _execution())

        assert result.payload.meaningful_progress is False
        assert result.payload.metadata["causal_override"] is True

    def test_high_confidence_with_path_does_not_trigger_override(self):
        graph = _CausalPathGraphPort({"path_exists": True, "path_confidence": 0.8})
        evaluator = Evaluator(graph_query_port=graph, limits=EvaluationLimits(causal_override_confidence_threshold=0.3))

        result = evaluator.evaluate(_state(), _perception(), _goal(), _execution())

        assert result.payload.meaningful_progress is True
        assert result.payload.metadata["causal_override"] is False

    def test_no_path_never_overrides_regardless_of_confidence(self):
        graph = _CausalPathGraphPort({"path_exists": False, "path_confidence": 0.0})
        evaluator = Evaluator(graph_query_port=graph, limits=EvaluationLimits(causal_override_confidence_threshold=0.3))

        result = evaluator.evaluate(_state(), _perception(), _goal(), _execution())

        assert result.payload.meaningful_progress is True
        assert result.payload.metadata["causal_override"] is False
