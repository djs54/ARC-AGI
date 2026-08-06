from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.executor import Executor
from agents.arc4.perceive import PerceiveAgent
from agents.arc4.types import PhaseStatus, PlanCandidate, WorkflowState


class _RecordingGraphPort:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls: list[object] = []

    def ingest_perception(self, perception):
        self.calls.append(perception)
        if self.should_fail:
            raise RuntimeError("graph unavailable")
        return {"status": "ok"}


class _RecordingTransport:
    def __init__(self, result=None, *, should_fail: bool = False) -> None:
        self.result = result
        self.should_fail = should_fail
        self.calls: list[tuple[str, dict, dict]] = []

    def execute_action(self, action_id, payload, context):
        self.calls.append((action_id, dict(payload), dict(context)))
        if self.should_fail:
            raise RuntimeError("harness unavailable")
        return self.result


def test_perceive_hash_is_stable_for_equivalent_observations():
    agent = PerceiveAgent()
    first_state = WorkflowState()
    second_state = WorkflowState()
    observation_a = {"grid": [[1, 0], [0, 2]], "step": 1}
    observation_b = {"step": 1, "grid": [[1, 0], [0, 2]], "extra": "noise"}

    first = agent.perceive(first_state, observation_a).payload
    second = agent.perceive(second_state, observation_b).payload

    assert first is not None
    assert second is not None
    assert first.grid_hash == second.grid_hash
    assert first.grid_shape == (2, 2)
    assert len(first.entities) == 2


def test_perceive_detects_repeated_hashes_via_workflow_state():
    agent = PerceiveAgent()
    state = WorkflowState()

    first = agent.perceive(state, {"grid": [[1, 0], [0, 0]]}).payload
    second = agent.perceive(state, {"grid": [[1, 0], [0, 0]]}).payload

    assert first is not None
    assert second is not None
    assert first.loop_signal is False
    assert first.repeated_grid_count == 1
    assert second.loop_signal is True
    assert second.repeated_grid_count == 2
    assert state.loop_history[-1] == second.grid_hash
    assert state.loop_history_pointer == len(state.loop_history) - 1


def test_perceive_best_effort_graph_write_is_non_fatal():
    graph_port = _RecordingGraphPort(should_fail=True)
    agent = PerceiveAgent(graph_query_port=graph_port)
    state = WorkflowState()

    result = agent.perceive(state, {"grid": [[3]]})

    assert result.status == PhaseStatus.OK
    assert result.payload is not None
    assert result.payload.metadata["graph_ingestion"] == "failed"
    assert len(graph_port.calls) == 1
    assert state.previous_grid_hash == result.payload.grid_hash


def test_executor_success_path_normalizes_tuple_transport_result():
    transport = _RecordingTransport(result=({"grid": [[2]], "guid": "guid-123"}, 1.0, False, "guid-123"))
    executor = Executor(transport=transport)
    state = WorkflowState()
    plan = PlanCandidate(action_id="ACTION6", expected_effect="shift")

    result = executor.execute(state, plan, {"game_id": "game-1", "guid": "guid-123", "step": 7})

    assert result.status == PhaseStatus.OK
    assert result.payload is not None
    assert result.payload.action_id == "ACTION6"
    assert result.payload.candidate is plan
    assert result.payload.observation == {"grid": [[2]], "guid": "guid-123"}
    assert result.payload.did_progress is True
    assert transport.calls == [("ACTION6", {}, {"game_id": "game-1", "guid": "guid-123", "step": 7})]
    assert result.payload.metadata["guid"] == "guid-123"


def test_executor_missing_transport_is_structured_crash():
    executor = Executor()
    result = executor.execute(WorkflowState(), PlanCandidate(action_id="ACTION1"), {"game_id": "game-1"})

    assert result.status == PhaseStatus.CRASH
    assert result.reason == "missing_execution_transport"
    assert result.payload is not None
    assert result.payload.metadata["failure_reason"] == "missing_execution_transport"
    assert result.payload.observation == {}


def test_executor_transport_failure_is_structured_crash():
    executor = Executor(transport=_RecordingTransport(should_fail=True))
    result = executor.execute(WorkflowState(), PlanCandidate(action_id="ACTION2"), {"game_id": "game-1"})

    assert result.status == PhaseStatus.CRASH
    assert result.reason == "harness unavailable"
    assert result.payload is not None
    assert result.payload.metadata["error_type"] == "RuntimeError"
    assert "harness unavailable" in result.payload.metadata["traceback"]