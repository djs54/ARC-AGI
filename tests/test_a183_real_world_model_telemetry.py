"""Tests for A183: telemetry.py's world_model_node_count/edge_count never
queried the real Kuzu graph -- they were perception.entities/goal.alternatives/
plan.alternatives list sizes. Confirmed live: across a full episode where
record_transition/record_rule_evidence genuinely succeeded every step,
world_model_edge_count stayed flat the whole time. Fix: WorkflowState now
tracks a running count of *confirmed* successful graph writes
(world_model_node_writes/world_model_edge_writes), incremented only when a
write's result status == "ok", and telemetry reads those directly instead of
the old unrelated-list-size proxy. See backlog/A183.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.evaluator import EvaluationLimits, Evaluator
from agents.arc4.perceive import PerceiveAgent
from agents.arc4.telemetry import ArcV2Telemetry
from agents.arc4.types import ExecutionResult, GoalHypothesis, PerceptionSnapshot, PlanCandidate, ResolvedGoal, WorkflowState


def _execution(action_id: str = "ACTION6") -> ExecutionResult:
    return ExecutionResult(
        action_id=action_id,
        candidate=PlanCandidate(action_id=action_id, goal_id="goal-1"),
        observation={"grid": "new-hash"},
        did_progress=True,
        metadata={"step": 1},
    )


def _goal() -> ResolvedGoal:
    return ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="test goal", confidence=0.8))


def _state() -> WorkflowState:
    return WorkflowState(step_index=0, action_attempt_counts={}, action_falsification_counts={}, consecutive_no_progress_count=0)


class TestPerceiveIncrementsNodeWritesOnRealSuccess:
    def test_ok_status_increments_node_writes(self):
        class _OkPort:
            def ingest_perception(self, snapshot):
                return {"status": "ok", "tool": "ingest_perception"}

        agent = PerceiveAgent(graph_query_port=_OkPort())
        state = _state()
        agent.perceive(state, {"grid": [[1, 2], [3, 4]]})
        assert state.world_model_node_writes == 1

    def test_capability_missing_does_not_increment(self):
        class _MissingPort:
            def ingest_perception(self, snapshot):
                return {"status": "capability_missing", "tool": "ingest_perception"}

        agent = PerceiveAgent(graph_query_port=_MissingPort())
        state = _state()
        agent.perceive(state, {"grid": [[1, 2], [3, 4]]})
        assert state.world_model_node_writes == 0

    def test_no_graph_port_does_not_increment(self):
        agent = PerceiveAgent(graph_query_port=None)
        state = _state()
        agent.perceive(state, {"grid": [[1, 2], [3, 4]]})
        assert state.world_model_node_writes == 0

    def test_exception_does_not_increment(self):
        class _BrokenPort:
            def ingest_perception(self, snapshot):
                raise RuntimeError("graph unavailable")

        agent = PerceiveAgent(graph_query_port=_BrokenPort())
        state = _state()
        agent.perceive(state, {"grid": [[1, 2], [3, 4]]})
        assert state.world_model_node_writes == 0


class TestEvaluatorIncrementsNodeWritesForTransitions:
    def test_ok_status_increments_node_writes(self):
        class _RecordingGraphPort:
            def record_transition(self, execution, grid_diff, entities):
                return {"status": "ok", "ok": True}

        perception = PerceptionSnapshot(
            observation={"grid": "hash-1"},
            grid_hash="hash-1",
            metadata={"grid_diff": {"changed_cells": [{"row": 0, "col": 0, "from": 1, "to": 2}], "changed_count": 1, "truncated": False}},
        )
        evaluator = Evaluator(graph_query_port=_RecordingGraphPort(), limits=EvaluationLimits())
        state = _state()

        evaluator.evaluate(state, perception, _goal(), _execution())

        assert state.world_model_node_writes == 1

    def test_capability_missing_does_not_increment(self):
        class _CapabilityMissingPort:
            def record_transition(self, execution, grid_diff, entities):
                return {"status": "capability_missing"}

        perception = PerceptionSnapshot(
            observation={"grid": "hash-1"},
            grid_hash="hash-1",
            metadata={"grid_diff": {"changed_cells": [{"row": 0, "col": 0, "from": 1, "to": 2}], "changed_count": 1, "truncated": False}},
        )
        evaluator = Evaluator(graph_query_port=_CapabilityMissingPort(), limits=EvaluationLimits())
        state = _state()

        evaluator.evaluate(state, perception, _goal(), _execution())

        assert state.world_model_node_writes == 0

    def test_no_diff_does_not_increment(self):
        class _RecordingGraphPort:
            def record_transition(self, execution, grid_diff, entities):
                raise AssertionError("should not be called when there's no diff")

        perception = PerceptionSnapshot(observation={"grid": "hash-1"}, grid_hash="hash-1")
        evaluator = Evaluator(graph_query_port=_RecordingGraphPort(), limits=EvaluationLimits())
        state = _state()

        evaluator.evaluate(state, perception, _goal(), _execution())

        assert state.world_model_node_writes == 0


class TestEvaluatorIncrementsEdgeWritesForRules:
    def test_ok_status_increments_edge_writes(self):
        class _RecordingGraphPort:
            def record_rule_evidence(self, execution, grid_diff):
                return {"status": "ok", "ok": True}

        perception = PerceptionSnapshot(
            observation={"grid": "hash-1"},
            grid_hash="hash-1",
            metadata={"grid_diff": {"changed_cells": [{"row": 0, "col": 0, "from": 1, "to": 2}], "changed_count": 1, "truncated": False}},
        )
        evaluator = Evaluator(graph_query_port=_RecordingGraphPort(), limits=EvaluationLimits())
        state = _state()

        evaluator.evaluate(state, perception, _goal(), _execution())

        assert state.world_model_edge_writes == 1

    def test_no_changes_status_does_not_increment(self):
        class _NoChangesPort:
            def record_rule_evidence(self, execution, grid_diff):
                return {"status": "no_changes", "recorded": False}

        perception = PerceptionSnapshot(
            observation={"grid": "hash-1"},
            grid_hash="hash-1",
            metadata={"grid_diff": {"changed_cells": [{"row": 0, "col": 0, "from": 1, "to": 2}], "changed_count": 1, "truncated": False}},
        )
        evaluator = Evaluator(graph_query_port=_NoChangesPort(), limits=EvaluationLimits())
        state = _state()

        evaluator.evaluate(state, perception, _goal(), _execution())

        assert state.world_model_edge_writes == 0

    def test_node_and_edge_writes_are_independent_counters(self):
        class _MixedPort:
            def record_transition(self, execution, grid_diff, entities):
                return {"status": "ok"}

            def record_rule_evidence(self, execution, grid_diff):
                return {"status": "capability_missing"}

        perception = PerceptionSnapshot(
            observation={"grid": "hash-1"},
            grid_hash="hash-1",
            metadata={"grid_diff": {"changed_cells": [{"row": 0, "col": 0, "from": 1, "to": 2}], "changed_count": 1, "truncated": False}},
        )
        evaluator = Evaluator(graph_query_port=_MixedPort(), limits=EvaluationLimits())
        state = _state()

        evaluator.evaluate(state, perception, _goal(), _execution())

        assert state.world_model_node_writes == 1
        assert state.world_model_edge_writes == 0


class TestWorkflowStateCountersSurviveRoundTrip:
    def test_to_dict_from_dict_preserves_write_counts(self):
        state = WorkflowState(world_model_node_writes=5, world_model_edge_writes=3)
        restored = WorkflowState.from_dict(state.to_dict())
        assert restored.world_model_node_writes == 5
        assert restored.world_model_edge_writes == 3


class TestTelemetryReadsRealCounters:
    def test_node_and_edge_count_read_from_state_not_unrelated_lists(self):
        telemetry = ArcV2Telemetry(task_id="t", game_id="g", append_snapshot=None)
        state = WorkflowState(world_model_node_writes=7, world_model_edge_writes=2)

        assert telemetry._world_model_node_count(state) == 7
        assert telemetry._world_model_edge_count(state) == 2

    def test_none_state_returns_zero(self):
        telemetry = ArcV2Telemetry(task_id="t", game_id="g", append_snapshot=None)
        assert telemetry._world_model_node_count(None) == 0
        assert telemetry._world_model_edge_count(None) == 0
