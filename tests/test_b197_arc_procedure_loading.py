"""Tests for B197: Load Procedures Before Solving

Verifies that the runner queries for procedures before solving and that the
SolveEngine initializes an active PlanChunk from a loaded procedure.
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock

from agents.arc3.solver import SolveEngine
from agents.arc3.runner import DurableARCRunner
from benchmarks.arc3.adapter import NoOpBrainClient
from agents.arc3.checkpoint import CheckpointManager


def _sample_task(task_id: str):
    from benchmarks.ab_harness import ABTask
    t = ABTask(task_id=task_id, category="c", prompt="p")
    setattr(t, "game_id", "g")
    return t


def _make_stub_harness() -> MagicMock:
    harness = MagicMock()
    harness.llm_client = None
    harness.serializer = MagicMock()
    harness.serializer._estimate_tokens.return_value = 1
    harness.config = MagicMock()
    harness.mock_api = True
    harness._get_mock_initial_frame = MagicMock(return_value={"frame": [[[0]]]})
    harness._execute_mock_action = MagicMock(return_value=( {"frame": [[[0]]], "state": "WIN", "available_actions": []}, 1.0, True))
    return harness


class SpyBrain(NoOpBrainClient):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def recall_procedures(self, *, archetype: str, limit: int = 3):
        self.calls.append(("recall_procedures", archetype, limit))
        return {"procedures": [{"procedure_id": "proc-1", "name": "TestProc", "steps_json": json.dumps([{"action":"ACTION1"}, {"action":"ACTION2"}])}]}


@pytest.mark.asyncio
async def test_solve_engine_initializes_with_procedure():
    # Build a SolveEngine with a procedure supplied and verify active chunk
    steps = [{"action": "ACTION1"}, {"action": "ACTION2"}]
    proc = {"procedure_id": "p1", "name": "P1", "steps_json": json.dumps(steps)}
    se = SolveEngine(brain_client=None, llm_client=None, session_id="s", emit_trace_event=None, cost_tracker=None, loaded_procedures=[proc])

    assert se._active_chunk is not None
    assert se._active_chunk.source == "procedure"
    assert se._active_chunk.estimated_actions == ["ACTION1", "ACTION2"]
    assert se._applied_procedure_id == "p1"


@pytest.mark.asyncio
async def test_runner_calls_recall_procedures(tmp_path):
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    harness = _make_stub_harness()
    spy = SpyBrain()
    runner = DurableARCRunner(harness, spy, config={"llm": {"model": "test"}})

    task = _sample_task("task-197")
    results = await runner.run([task], "card-b197")

    # Ensure recall_procedures was called
    assert any(c[0] == "recall_procedures" for c in spy.calls), "recall_procedures was not invoked"


@pytest.mark.asyncio
async def test_noop_brain_returns_empty(tmp_path):
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    harness = _make_stub_harness()
    noop = NoOpBrainClient()
    runner = DurableARCRunner(harness, noop, config={"llm": {"model": "test"}})

    task = _sample_task("task-197b")
    results = await runner.run([task], "card-b197b")

    # Run completed without exception and returned a result list
    assert isinstance(results, list)