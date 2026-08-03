"""Tests for B190: Cross-Puzzle Learning via Task Graph.

Verifies that the runner registers a task graph at batch start, stores
lessons after a puzzle completes, and advances the task node in the
registered graph. Also checks that subsequent puzzles call recall_relevant_lessons.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock

from benchmarks.ab_harness import ABTask, ABTaskResult, ABVariant, BenchmarkConfig
from benchmarks.arc3.adapter import NoOpBrainClient
from agents.arc3.runner import DurableARCRunner
from agents.arc3.checkpoint import CheckpointManager


def _sample_task(task_id: str) -> ABTask:
    t = ABTask(task_id=task_id, category="c", prompt="p")
    setattr(t, "game_id", "g")
    return t


def _make_stub_harness() -> MagicMock:
    harness = MagicMock()
    harness.llm_client = None
    harness.serializer = MagicMock()
    harness.serializer._estimate_tokens.return_value = 1
    harness.config = BenchmarkConfig(name="dummy", parameters={"max_attempts_per_puzzle": 3})
    harness.mock_api = True
    harness._get_mock_initial_frame = MagicMock(return_value={"frame": [[[0]]]})
    # make actions immediately win to trigger lesson persistence
    harness._execute_mock_action = MagicMock(return_value=( {"frame": [[[0]]], "state": "WIN", "available_actions": []}, 1.0, True))
    return harness


class SpyBrain(NoOpBrainClient):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def register_task_graph(self, *, label: str, session_id: str, owner: str, tasks: list[dict]):
        self.calls.append(("register_task_graph", label, session_id, owner, tasks))
        return await super().register_task_graph(label=label, session_id=session_id, owner=owner, tasks=tasks)

    async def store_lesson(self, *, content: str, tags: list[str], session_id: str):
        self.calls.append(("store_lesson", content, tags, session_id))
        return await super().store_lesson(content=content, tags=tags, session_id=session_id)

    async def advance_task(self, *, graph_id: str, task_id: str, status: str, result: str | None = None):
        self.calls.append(("advance_task", graph_id, task_id, status, result))
        return await super().advance_task(graph_id=graph_id, task_id=task_id, status=status, result=result)

    async def recall_relevant_lessons(self, *, query: str, limit: int):
        self.calls.append(("recall_relevant_lessons", query, limit))
        return await super().recall_relevant_lessons(query=query, limit=limit)


@pytest.mark.asyncio
async def test_register_store_and_advance_calls(tmp_path):
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    harness = _make_stub_harness()
    spy = SpyBrain()
    runner = DurableARCRunner(harness, spy, config={"llm": {"model": "test"}})

    task = _sample_task("task-1")
    results = await runner.run([task], "card-b190")

    # Ensure the run completed and produced a result
    assert isinstance(results, list) and len(results) >= 1

    # The Spy should have recorded register, a lesson persistence call, and advance calls
    assert any(c[0] == "register_task_graph" for c in spy.calls), "register_task_graph not called"
    assert any(c[0] in ("store_lesson", "upsert_lesson") for c in spy.calls), "store_lesson/upsert_lesson not called"
    assert any(c[0] == "advance_task" for c in spy.calls), "advance_task not called"


@pytest.mark.asyncio
async def test_lessons_recalled_on_subsequent_run(tmp_path):
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    harness = _make_stub_harness()
    spy = SpyBrain()
    runner1 = DurableARCRunner(harness, spy, config={"llm": {"model": "test"}})

    task1 = _sample_task("task-A")
    await runner1.run([task1], "card-b190-2")

    # First run should have persisted a lesson (store or upsert)
    assert len(spy._lessons_store) >= 1
    assert any(c[0] in ("store_lesson", "upsert_lesson") for c in spy.calls)

    # Clear calls and run a second puzzle — orchestrator.perceive should call recall_relevant_lessons
    spy.calls.clear()
    runner2 = DurableARCRunner(harness, spy, config={"llm": {"model": "test"}})
    task2 = _sample_task("task-B")
    await runner2.run([task2], "card-b190-2")

    assert any(c[0] == "recall_relevant_lessons" for c in spy.calls), "recall_relevant_lessons was not invoked on subsequent run"