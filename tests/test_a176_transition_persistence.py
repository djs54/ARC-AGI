"""Tests for A176: A170's grid diffs were real, tested, working evidence that
got computed fresh each step and discarded -- the graph, the one component
designed to accumulate evidence, never saw it. This persists the diff as a
graph State Node (summarized as a bounded color-transition histogram,
attributed to an A175 entity_ref when determinable) and wires a consumer
query (entity transition history) into goal resolution. See backlog/A176.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.evaluator import EvaluationLimits, Evaluator
from agents.arc4.goal_resolver import GoalResolver, GoalResolverLimits
from agents.arc4.graph_queries import ArcGraphQueryPort
from agents.arc4.types import ExecutionResult, GoalHypothesis, PerceivedEntity, PerceptionSnapshot, PlanCandidate, ResolvedGoal, WorkflowState


class _StubBrainClient:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result if result is not None else {"ok": True}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, payload))
        return self.result


def _execution(action_id: str = "ACTION6") -> ExecutionResult:
    return ExecutionResult(
        action_id=action_id,
        candidate=PlanCandidate(action_id=action_id, goal_id="goal-1"),
        observation={"grid": "new-hash"},
        did_progress=True,
        metadata={"step": 3},
    )


class TestRecordTransitionWritePayload:
    def test_summarizes_diff_as_color_transition_histogram(self):
        stub = _StubBrainClient()
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        grid_diff = {
            "changed_cells": [
                {"row": 0, "col": 0, "from": 2, "to": 5},
                {"row": 0, "col": 1, "from": 2, "to": 5},
                {"row": 1, "col": 0, "from": 1, "to": 9},
            ],
            "changed_count": 3,
            "truncated": False,
        }

        result = port.record_transition(_execution(), grid_diff)

        assert result["ok"] is True
        tool_name, payload = stub.calls[0]
        assert tool_name == "arc_record_transition"
        assert payload["task_id"] == "task-1"
        assert payload["step"] == 3
        assert payload["action_id"] == "ACTION6"
        assert payload["changed_count"] == 3
        assert {"from": 2, "to": 5, "count": 2} in payload["color_transitions"]
        assert {"from": 1, "to": 9, "count": 1} in payload["color_transitions"]

    def test_no_changes_skips_the_call_entirely(self):
        stub = _StubBrainClient()
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        result = port.record_transition(_execution(), {"changed_cells": [], "changed_count": 0, "truncated": False})
        assert result == {"status": "no_changes", "recorded": False}
        assert stub.calls == []

    def test_attributes_transition_to_entity_whose_bbox_contains_changed_cells(self):
        stub = _StubBrainClient()
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        grid_diff = {"changed_cells": [{"row": 1, "col": 1, "from": 0, "to": 5}], "changed_count": 1, "truncated": False}
        entity = PerceivedEntity(kind="point", value="5", attributes={"bbox": (0, 0, 2, 2), "entity_ref": 7})

        port.record_transition(_execution(), grid_diff, entities=[entity])

        _, payload = stub.calls[0]
        assert payload["entity_ref"] == 7

    def test_capability_missing_degrades_cleanly(self):
        stub = _StubBrainClient(result={"status": "capability_missing"})
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        grid_diff = {"changed_cells": [{"row": 0, "col": 0, "from": 1, "to": 2}], "changed_count": 1, "truncated": False}
        result = port.record_transition(_execution(), grid_diff)
        assert result["status"] == "capability_missing"


class TestFetchEntityHistory:
    def test_returns_real_history_when_available(self):
        stub = _StubBrainClient(result={"transitions": [{"action_id": "ACTION6", "step": 2}], "changed_count_total": 5})
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        history = port.fetch_entity_history(entity_ref=1)
        assert history["changed_count_total"] == 5
        assert len(history["transitions"]) == 1

    def test_capability_missing_returns_empty_history(self):
        stub = _StubBrainClient(result={"status": "capability_missing"})
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        history = port.fetch_entity_history(entity_ref=1)
        assert history == {"transitions": [], "changed_count_total": 0}


class TestEvaluatorWiresTransitionRecording:
    def test_evaluator_calls_record_transition_when_diff_present(self):
        calls: list[Any] = []

        class _RecordingGraphPort:
            def record_transition(self, execution, grid_diff, entities):
                calls.append((execution.action_id, grid_diff, list(entities)))
                return {"ok": True}

        perception = PerceptionSnapshot(
            observation={"grid": "hash-1"},
            grid_hash="hash-1",
            metadata={"grid_diff": {"changed_cells": [{"row": 0, "col": 0, "from": 1, "to": 2}], "changed_count": 1, "truncated": False}},
        )
        goal = ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="test goal", confidence=0.8))
        evaluator = Evaluator(graph_query_port=_RecordingGraphPort(), limits=EvaluationLimits())
        state = WorkflowState(step_index=0, action_attempt_counts={}, action_falsification_counts={}, consecutive_no_progress_count=0)

        result = evaluator.evaluate(state, perception, goal, _execution())

        assert len(calls) == 1
        assert result.payload.metadata["transition_recording"] == "ok"

    def test_evaluator_skips_when_no_diff(self):
        class _RecordingGraphPort:
            def record_transition(self, execution, grid_diff, entities):
                raise AssertionError("should not be called when there's no diff")

        perception = PerceptionSnapshot(observation={"grid": "hash-1"}, grid_hash="hash-1")
        goal = ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="test goal", confidence=0.8))
        evaluator = Evaluator(graph_query_port=_RecordingGraphPort(), limits=EvaluationLimits())
        state = WorkflowState(step_index=0, action_attempt_counts={}, action_falsification_counts={}, consecutive_no_progress_count=0)

        result = evaluator.evaluate(state, perception, goal, _execution())
        assert result.payload.metadata["transition_recording"] == "skipped"


class TestGoalResolverConsumesEntityHistory:
    def test_entity_with_history_gets_confidence_boost(self):
        class _HistoryGraphPort:
            def fetch_goal_evidence(self, perception, goal=None):
                return {}

            def fetch_entity_history(self, entity_ref):
                return {"transitions": [{"action_id": "ACTION6"}], "changed_count_total": 3}

        perception = PerceptionSnapshot(
            observation={"grid": "hash-1"},
            grid_hash="hash-1",
            entities=(PerceivedEntity(kind="point", value="5", attributes={"entity_ref": 1}),),
        )
        resolver = GoalResolver(GoalResolverLimits())
        state = WorkflowState()

        with_history = resolver.resolve(state, perception, graph_port=_HistoryGraphPort()).payload
        without_history_resolver = GoalResolver(GoalResolverLimits())
        without_history = without_history_resolver.resolve(WorkflowState(), perception, graph_port=None).payload

        assert with_history.selected.confidence > without_history.selected.confidence
        assert "entity_history:has_changed" in with_history.selected.evidence

    def test_entity_without_history_gets_no_boost(self):
        class _EmptyHistoryGraphPort:
            def fetch_goal_evidence(self, perception, goal=None):
                return {}

            def fetch_entity_history(self, entity_ref):
                return {"transitions": [], "changed_count_total": 0}

        perception = PerceptionSnapshot(
            observation={"grid": "hash-1"},
            grid_hash="hash-1",
            entities=(PerceivedEntity(kind="point", value="5", attributes={"entity_ref": 1}),),
        )
        resolver = GoalResolver(GoalResolverLimits())
        result = resolver.resolve(WorkflowState(), perception, graph_port=_EmptyHistoryGraphPort()).payload
        assert "entity_history:has_changed" not in result.selected.evidence
