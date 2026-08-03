from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from benchmarks.arc3.state_serializer import StateSerializerForARC
from agents.arc3.orchestrator import ARCOrchestrator


@pytest.fixture
def brain():
    client = MagicMock()
    client.notify_turn = AsyncMock(return_value={"status": "ok"})
    client.register_plan = AsyncMock(return_value={"plan_id": "plan-1"})
    client.report_outcome = AsyncMock(return_value={"status": "ok"})
    client.store_lesson = AsyncMock(return_value={"status": "stored"})
    return client


@pytest.fixture
def orchestrator(brain):
    return ARCOrchestrator(
        brain_client=brain,
        llm_client=None,
        session_id="b165-session",
        serializer=StateSerializerForARC(),
        config={},
    )


def test_extract_run_lessons_identifies_zero_effect_actions(orchestrator):
    orchestrator._task_id = "arc_eval_001"
    orchestrator._game_id = "game-1"
    orchestrator._step_history = [{"action_id": "ACTION1"}, {"action_id": "ACTION5"}]
    orchestrator._solve_context = {
        "archetype": "space",
        "victory": "reach_goal",
        "strategy_summary": "try moving toward the goal",
    }
    orchestrator.observed_action_effects = {
        "ACTION1": {"avg_pixels_changed": 0, "avg_reward": 0.0, "times_seen": 2, "value_status": "low_value"},
        "ACTION5": {"avg_pixels_changed": 42, "avg_reward": 0.0, "times_seen": 1, "value_status": "valuable"},
    }

    lesson = orchestrator._extract_run_lessons(False)

    assert "ACTION1" in lesson["zero_effect_actions"]
    assert "ACTION5" in lesson["effective_actions"]
    assert lesson["outcome"] == "failed"


@pytest.mark.asyncio
async def test_evaluate_stores_run_lesson_and_analogy(orchestrator, brain):
    orchestrator._task_id = "arc_eval_001"
    orchestrator._game_id = "game-1"
    orchestrator._solve_context = {
        "archetype": "space",
        "victory": "reach_goal",
        "strategy_summary": "move up then interact",
    }
    orchestrator.observed_action_effects = {
        "ACTION2": {"avg_pixels_changed": 12, "avg_reward": 0.0, "times_seen": 1, "value_status": "valuable"}
    }

    final_observation = {
        "task_id": "arc_eval_001",
        "grid": [[0, 1], [1, 0]],
        "available_actions": ["ACTION1", "ACTION2"],
    }

    await orchestrator.evaluate(False, 5, 15, final_observation)

    assert brain.store_lesson.await_count >= 1
    stored_content = brain.store_lesson.await_args.kwargs["content"]
    assert "action_effects" in stored_content

    assert any(
        "[PUZZLE ANALOGY]" in call.kwargs.get("content", "")
        for call in brain.notify_turn.await_args_list
    )
