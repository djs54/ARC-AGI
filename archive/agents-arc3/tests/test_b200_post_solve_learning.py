"""Tests for B200: Post-solve reporting and lesson upsert.

Verifies that after a puzzle completes the runner calls `report_outcome`
and `upsert_lesson` with structured payloads.
"""

from __future__ import annotations

import json
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

    async def report_outcome(
        self,
        *,
        plan_id: str | None = None,
        outcome: str | None = None,
        outcome_text: str | None = None,
        valence: float = 0.0,
        session_id: str = "",
        evidence: dict | None = None,
        valence_source: str | None = None,
    ) -> dict:
        self.calls.append(("report_outcome", plan_id, valence, outcome_text, session_id))
        return await super().report_outcome(plan_id=plan_id, outcome=outcome, outcome_text=outcome_text, valence=valence, session_id=session_id, evidence=evidence, valence_source=valence_source)

    async def upsert_lesson(self, *, domain: str, text: str, valence: float, confidence: float = 0.7, tags: list[str] | None = None) -> dict:
        self.calls.append(("upsert_lesson", domain, text, valence, confidence, tags))
        return await super().upsert_lesson(domain=domain, text=text, valence=valence, confidence=confidence, tags=tags)


@pytest.mark.asyncio
async def test_report_and_upsert_called(tmp_path):
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    harness = _make_stub_harness()
    spy = SpyBrain()
    runner = DurableARCRunner(harness, spy, config={"llm": {"model": "test"}})

    task = _sample_task("task-200")
    results = await runner.run([task], "card-b200")

    # Ensure the run completed and produced a result
    assert isinstance(results, list) and len(results) >= 1

    # The Spy should have recorded report_outcome and upsert_lesson
    assert any(c[0] == "report_outcome" for c in spy.calls), "report_outcome not called"
    assert any(c[0] == "upsert_lesson" for c in spy.calls), "upsert_lesson not called"
    upserts = [c for c in spy.calls if c[0] == "upsert_lesson"]
    assert len(upserts) >= 2, "expected structured post-solve upsert lessons"
    assert any('"lesson_type": "run_summary"' in c[2] for c in upserts), "run_summary lesson missing"
