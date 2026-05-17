"""Tests for DurableARCRunner and loop worker robustness."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from benchmarks.ab_harness import ABTask, ABTaskResult, ABVariant, BenchmarkConfig
# SubmissionRunner is imported lazily within tests that need it to avoid
# requiring optional runtime dependencies at module import time.
from benchmarks.arc3.adapter import NoOpBrainClient
from agents.arc3.checkpoint import CheckpointManager
from agents.arc3.runner import DurableARCRunner


def _sample_tasks() -> list[ABTask]:
    tasks = [
        ABTask(task_id="task-1", category="c", prompt="p1"),
        ABTask(task_id="task-2", category="c", prompt="p2"),
    ]
    for task in tasks:
        setattr(task, "game_id", "g")
    return tasks


def _make_stub_harness() -> MagicMock:
    harness = MagicMock()
    harness.llm_client = None
    harness.serializer = MagicMock()
    harness.serializer._estimate_tokens.return_value = 1
    harness.config = BenchmarkConfig(name="dummy", parameters={"max_attempts_per_puzzle": 3})
    harness.mock_api = True
    harness._get_mock_initial_frame = MagicMock(return_value={"frame": [[[0]]]})
    harness._execute_mock_action = MagicMock(return_value=({"frame": [[[0]]]}, 1.0, True))
    return harness



@pytest.mark.asyncio
async def test_skips_completed_tasks(tmp_path):
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    tasks = _sample_tasks()
    harness = _make_stub_harness()
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})

    mgr = CheckpointManager("card-1")
    checkpoint = mgr.load_or_create(tasks)
    checkpoint.tasks["task-1"].status = "complete"
    checkpoint.tasks["task-1"].result = {
        "task_id": "task-1",
        "steps": 2,
        "runtime_seconds": 0,
        "final_state": "WIN",
        "final_observation": {"grid": [[0]]},
    }
    mgr.save(checkpoint)

    runner._run_puzzle = AsyncMock(return_value=(
        ABTaskResult(task_id="task-2", variant=ABVariant.SIDEQUESTS, correct=True, steps=1, tokens_input=1, tokens_output=1),
        0.1,
    ))

    results = await runner.run(tasks, "card-1")
    assert len(results) == 2
    runner._run_puzzle.assert_called_once()
    assert results[0]["task_id"] == "task-1"
    assert results[1]["task_id"] == "task-2"


@pytest.mark.asyncio
async def test_reruns_stale_completed_checkpoint_result(tmp_path):
    """A completed checkpoint without terminal payload should be re-run."""
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    tasks = _sample_tasks()
    harness = _make_stub_harness()
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})

    mgr = CheckpointManager("card-stale")
    checkpoint = mgr.load_or_create(tasks)
    checkpoint.tasks["task-1"].status = "complete"
    checkpoint.tasks["task-1"].result = {
        "task_id": "task-1",
        "steps": 2,
        "runtime_seconds": 0,
        # intentionally missing final_state/final_observation
    }
    mgr.save(checkpoint)

    run1 = ABTaskResult(
        task_id="task-1",
        variant=ABVariant.SIDEQUESTS,
        correct=True,
        steps=1,
        tokens_input=1,
        tokens_output=1,
        final_state="WIN",
        final_observation={"grid": [[1]]},
    )
    run2 = ABTaskResult(
        task_id="task-2",
        variant=ABVariant.SIDEQUESTS,
        correct=True,
        steps=1,
        tokens_input=1,
        tokens_output=1,
        final_state="WIN",
        final_observation={"grid": [[2]]},
    )
    runner._run_puzzle = AsyncMock(side_effect=[(run1, 0.1), (run2, 0.1)])

    results = await runner.run(tasks, "card-stale")

    assert runner._run_puzzle.call_count == 2
    assert [r["task_id"] for r in results] == ["task-1", "task-2"]
    assert results[0]["task_id"] == "task-1"
    assert results[0]["confidence"] == [1.0]


@pytest.mark.asyncio
async def test_continues_after_task_failure(tmp_path):
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    tasks = _sample_tasks()
    harness = _make_stub_harness()
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})

    async def _side_effect(*_):
        raise RuntimeError("boom")

    success_result = ABTaskResult(
        task_id="task-2",
        variant=ABVariant.SIDEQUESTS,
        correct=True,
        steps=1,
        tokens_input=1,
        tokens_output=1,
    )

    runner._run_puzzle = AsyncMock(side_effect=[RuntimeError("boom"), (success_result, 0.2)])

    results = await runner.run(tasks, "card-2")
    assert len(results) == 2
    runner._run_puzzle.assert_called()

    # Verify both results are present in output
    assert results[0]["task_id"] == "task-1"
    assert results[0]["correct"] is False
    assert results[0].get("failure_class") == "crash"
    assert results[1]["task_id"] == "task-2"
    assert results[1].get("correct") is True

    # Verify checkpoint state matches
    mgr = CheckpointManager("card-2")
    mgr.CHECKPOINT_DIR = tmp_path
    cp = mgr.load_or_create(tasks)
    assert cp.tasks["task-1"].status == "failed"
    assert cp.tasks["task-1"].result["failure_class"] == "crash"
    assert cp.tasks["task-2"].status == "complete"


@pytest.mark.asyncio
async def test_loop_worker_survives_error(monkeypatch):
    from benchmarks.arc3.submission import SubmissionRunner
    runner = SubmissionRunner()
    runner.db = MagicMock()
    runner.config = {"llm": {"provider": "ollama", "model": "test"}}

    fake_llm = MagicMock()
    monkeypatch.setattr("mcp_engine.llm.provider.create_llm_client", MagicMock(return_value=fake_llm))

    call_order = []

    async def _fake_run_loop(**kwargs):
        call_order.append(kwargs)
        if len(call_order) == 1:
            raise RuntimeError("boom")
        return True

    monkeypatch.setattr("mcp_engine.loop.orchestrator.run_loop", _fake_run_loop)

    worker = asyncio.create_task(runner._loop_worker([]))
    await runner.loop_queue.put(("id1", "text", "user", "session"))
    await runner.loop_queue.put(("id2", "text", "user", "session"))
    await asyncio.wait_for(runner.loop_queue.join(), timeout=2)
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert len(call_order) == 2


@pytest.mark.asyncio
async def test_noop_client_all_methods():
    client = NoOpBrainClient()
    plan = await client.register_plan(goal="g", steps=["a"], session_id="s")
    assert plan["plan_id"] is None
    outcome = await client.report_outcome(plan_id="p", outcome="ok", valence=0.5, session_id="s")
    assert outcome["updated"] is False
    recalled = await client.recall_plans(goal_query="g", session_id="s", min_valence=0.0, limit=5)
    assert recalled["plans"] == []
    lessons = await client.recall_relevant_lessons(query="q", limit=5)
    assert lessons["lessons"] == []
    analogies = await client.analogical_search(query="q", current_quest_id="c", limit=5, min_similarity=0.7)
    assert analogies["results"] == []


@pytest.mark.asyncio
async def test_noop_client_branch_quest():
    client = NoOpBrainClient()
    result = await client.branch_quest(name="test", purpose="p", parent_quest_id="q")
    assert result["side_quest_id"] is None


@pytest.mark.asyncio
async def test_state_win_stops_puzzle(tmp_path):
    """When state is WIN, _run_puzzle should set success=True even with reward=0."""
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    harness = _make_stub_harness()
    # Return frame with WIN state, reward=0, done=False to ensure state drives the outcome
    harness._execute_mock_action = MagicMock(
        return_value=({"frame": [[[0]]], "state": "WIN", "available_actions": [], "levels_completed": 1, "win_levels": 1}, 0.0, False)
    )
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})
    tasks = _sample_tasks()
    task = tasks[0]
    from agents.arc3.orchestrator import ARCOrchestrator
    from benchmarks.arc3.state_serializer import StateSerializerForARC
    orch = ARCOrchestrator(
        brain_client=NoOpBrainClient(),
        llm_client=None,
        session_id="s",
        serializer=StateSerializerForARC(),
        config={},
    )
    result, _ = await runner._run_puzzle(orch, task)
    assert result.correct is True


@pytest.mark.asyncio
async def test_state_game_over_retries_then_fails(tmp_path):
    """When state is GAME_OVER on every attempt, _run_puzzle retries then fails."""
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    harness = _make_stub_harness()
    harness._execute_mock_action = MagicMock(
        return_value=({"frame": [[[0]]], "state": "GAME_OVER", "available_actions": []}, 0.0, False)
    )
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}, "max_retries_per_puzzle": 2})
    tasks = _sample_tasks()
    task = tasks[0]
    from agents.arc3.orchestrator import ARCOrchestrator
    from benchmarks.arc3.state_serializer import StateSerializerForARC
    orch = ARCOrchestrator(
        brain_client=NoOpBrainClient(),
        llm_client=None,
        session_id="s",
        serializer=StateSerializerForARC(),
        config={},
    )
    result, _ = await runner._run_puzzle(orch, task)
    assert result.correct is False
    assert "2 attempt" in result.response_text


@pytest.mark.asyncio
async def test_game_over_then_win_on_retry(tmp_path):
    """Agent fails first attempt with GAME_OVER, wins on second attempt."""
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    harness = _make_stub_harness()
    # First call: GAME_OVER, second call: WIN
    harness._execute_mock_action = MagicMock(
        side_effect=[
            ({"frame": [[[0]]], "state": "GAME_OVER", "available_actions": []}, 0.0, False),
            ({"frame": [[[0]]], "state": "WIN", "available_actions": [], "levels_completed": 1, "win_levels": 1}, 1.0, True),
        ]
    )
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}, "max_retries_per_puzzle": 3})
    tasks = _sample_tasks()
    task = tasks[0]
    from agents.arc3.orchestrator import ARCOrchestrator
    from benchmarks.arc3.state_serializer import StateSerializerForARC
    orch = ARCOrchestrator(
        brain_client=NoOpBrainClient(),
        llm_client=None,
        session_id="s",
        serializer=StateSerializerForARC(),
        config={},
    )
    result, _ = await runner._run_puzzle(orch, task)
    assert result.correct is True


@pytest.mark.asyncio
async def test_real_api_path_uses_http_session(tmp_path):
    """When mock_api is false, the durable runner should call the HTTP session."""
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    harness = _make_stub_harness()
    harness.mock_api = False
    harness._session = AsyncMock()
    harness._session.post = AsyncMock(
        side_effect=[
            MagicMock(
                json=MagicMock(return_value={"card_id": "card-123"}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(
                    return_value={"frame": [[[1]]], "state": "NOT_FINISHED", "guid": "guid-1"}
                ),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(
                    return_value={"frame": [[[1]]], "state": "WIN", "guid": "guid-2", "levels_completed": 1, "win_levels": 1}
                ),
                raise_for_status=MagicMock(),
            ),
        ]
    )
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})
    task = _sample_tasks()[0]

    from agents.arc3.orchestrator import ARCOrchestrator
    from benchmarks.arc3.state_serializer import StateSerializerForARC

    orch = ARCOrchestrator(
        brain_client=NoOpBrainClient(),
        llm_client=None,
        session_id="s",
        serializer=StateSerializerForARC(),
        config={},
    )

    result, _ = await runner._run_puzzle(orch, task)

    assert result.correct is True
    assert harness._session.post.await_count == 3
    harness._get_mock_initial_frame.assert_not_called()
    assert result.steps == 1


@pytest.mark.asyncio
async def test_run_calls_branch_quest_per_task(tmp_path):
    """Each puzzle should get its own branch_quest call."""
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    tasks = _sample_tasks()
    harness = _make_stub_harness()

    # DurableARCRunner wraps this brain in a LedgerBrainClient
    inner_brain = MagicMock(spec=NoOpBrainClient)
    inner_brain.branch_quest = AsyncMock(return_value={"side_quest_id": "sq-1"})
    inner_brain.db = None

    runner = DurableARCRunner(harness, inner_brain, config={"llm": {"model": "test"}})
    runner._run_puzzle = AsyncMock(return_value=(
        ABTaskResult(task_id="task-1", variant=ABVariant.SIDEQUESTS, correct=True, steps=1, tokens_input=1, tokens_output=1),
        0.1,
    ))
    await runner.run(tasks, "card-branch")

    assert inner_brain.branch_quest.call_count == len(tasks)
@pytest.mark.asyncio
async def test_progress_callback_receives_step_snapshots(tmp_path):
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    harness = _make_stub_harness()
    snapshots = []
    runner = DurableARCRunner(
        harness,
        NoOpBrainClient(),
        config={"llm": {"model": "test"}},
        progress_callback=snapshots.append,
    )
    task = _sample_tasks()[0]

    from agents.arc3.orchestrator import ARCOrchestrator
    from benchmarks.arc3.state_serializer import StateSerializerForARC

    orch = ARCOrchestrator(
        brain_client=NoOpBrainClient(),
        llm_client=None,
        session_id="s",
        serializer=StateSerializerForARC(),
        config={},
    )

    result, _ = await runner._run_puzzle(orch, task)

    assert result.steps >= 1
    assert len(snapshots) >= 1
    assert snapshots[0]["snapshot_type"] == "step"
    assert snapshots[0]["task_id"] == task.task_id
    assert snapshots[0]["step"] == 1
    assert "env_reward" in snapshots[0]
    assert "progress_reward" in snapshots[0]
    assert "reward_components" in snapshots[0]
    assert "env_signals" in snapshots[0]
    assert "solve_phase_summary" in snapshots[0]


def test_submission_row_includes_debug_fields():
    harness = _make_stub_harness()
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})

    row = runner._submission_row_from_result(
        {
            "game_id": "live-game-123",
            "task_id": "arc_eval_001",
            "correct": False,
            "runtime_seconds": 12.34,
            "steps": 2,
            "tokens_input": 10,
            "tokens_output": 3,
            "final_state": "NOT_FINISHED",
            "final_observation": {"grid": [[1, 2], [3, 4]]},
            "bootstrap_write_trace": [
                {
                    "phase": "bootstrap",
                    "kind": "notify_turn",
                    "summary": "ingested structure",
                }
            ],
            "final_write_trace": [
                {
                    "phase": "finalization",
                    "kind": "report_outcome",
                    "summary": "plan plan-1 outcome=failed valence=-0.70",
                }
            ],
            "debug_steps": [
                {
                    "step": 1,
                    "state_before": "NOT_STARTED",
                    "board_before": {
                        "frame_hash": "before123",
                        "rows": 2,
                        "cols": 2,
                        "top_colors": [{"value": 1, "count": 2}],
                        "coarse_map": "1 1\n1 1",
                    },
                    "available_actions": ["ACTION1", "ACTION6"],
                    "prompt": "prompt 1",
                    "action_id": "ACTION1",
                    "rationale": "test move",
                    "reward": 0.0,
                    "done": False,
                    "state_after": "NOT_FINISHED",
                    "board_after": {
                        "frame_hash": "after123",
                        "rows": 2,
                        "cols": 2,
                        "top_colors": [{"value": 2, "count": 2}],
                        "coarse_map": "2 2\n2 2",
                    },
                    "write_trace": [
                        {
                            "phase": "step-1",
                            "kind": "hypothesis_update",
                            "summary": "ACTION1 -> tentative_progress (score 0.41); facts=1 paths=1",
                            "detail": {
                                "saved_action_facts": [
                                    {
                                        "action": "ACTION1",
                                        "fact_type": "deterministic_effect",
                                        "value_status": "tentative",
                                        "trend": {
                                            "kind": "directional_drift",
                                            "axis": "col",
                                            "direction": "left",
                                            "avg_delta": 1.0,
                                        },
                                    }
                                ],
                                "saved_path_hypotheses": [
                                    {
                                        "actions": ["ACTION1", "ACTION2"],
                                        "value_status": "tentative",
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
    )

    assert row["game_id"] == "live-game-123"
    assert row["task_id"] == "arc_eval_001"
    assert row["correct"] is False
    assert row["steps"] == 2
    assert "final_grid" not in row
    assert row["bootstrap_write_trace"][0]["kind"] == "notify_turn"
    assert "predictions" not in row
    assert row["progress_log"][0]["action_id"] == "ACTION1"
    assert row["progress_log"][0]["board_before"]["frame_hash"] == "before123"
    assert row["progress_log"][0]["board_after"]["frame_hash"] == "after123"
    assert row["progress_log"][0]["write_trace"][0]["kind"] == "hypothesis_update"
    assert row["progress_log"][0]["write_trace"][0]["detail"]["saved_action_facts"][0]["action"] == "ACTION1"
    assert row["progress_log"][0]["write_trace"][0]["detail"]["saved_action_facts"][0]["trend"]["direction"] == "left"
    assert row["progress_log"][0]["write_trace"][0]["detail"]["saved_path_hypotheses"][0]["actions"] == ["ACTION1", "ACTION2"]
    assert row["prompt_trace"][0]["prompt"] == "prompt 1"
    assert row["prompt_trace"][0]["block_trace"] == []
    assert row["confidence"] == [0.0]
    assert row["final_write_trace"][0]["kind"] == "report_outcome"


def test_submission_row_extracts_prompt_block_trace():
    harness = _make_stub_harness()
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})

    prompt = (
        "SYSTEM: test\n\n"
        "STATE: NOT_FINISHED  ENERGY: 100%\n\n"
        "=== SOLVE CONTEXT ===\n"
        "ARCHETYPE: space (confidence=0.65)\n"
        "ACTIVE CHUNK: Move reach_goal toward goal [directional]\n\n"
        "=== ACTION FACTS ===\n"
        "ACTION6: LOW_VALUE\n\n"
        "=== PATH HYPOTHESES ===\n"
        "UNTESTED: ACTION6\n\n"
        "=== OBSERVATION ===\n"
        "Grid: 64x64\n\n"
        "INSTRUCTION: Choose next action"
    )

    row = runner._submission_row_from_result(
        {
            "task_id": "arc_eval_001",
            "game_id": "game-1",
            "steps": 1,
            "correct": False,
            "runtime_seconds": 1.0,
            "final_state": "NOT_FINISHED",
            "final_observation": {"grid": [[1]]},
            "debug_steps": [
                {
                    "step": 1,
                    "available_actions": ["ACTION6"],
                    "prompt": prompt,
                }
            ],
        }
    )

    block_trace = row["prompt_trace"][0]["block_trace"]
    assert [b["block"] for b in block_trace] == [
        "SolveContextBlock",
        "ChunkBlock",
        "ActionFactBlock",
        "PathHypothesisBlock",
        "ObservationBlock",
        "InstructionBlock",
    ]
    assert block_trace[0]["tool"] == "ARC Agent SolveEngine"
    assert block_trace[1]["block"] == "ChunkBlock"


def test_submission_row_includes_orchestration_report_with_no_violations():
    harness = _make_stub_harness()
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})

    row = runner._submission_row_from_result(
        {
            "task_id": "arc_eval_001",
            "game_id": "game-1",
            "steps": 1,
            "correct": False,
            "runtime_seconds": 1.0,
            "final_state": "NOT_FINISHED",
            "final_observation": {"grid": [[1]]},
            "sidequests_ledger": [
                {
                    "step": 0,
                    "phase": "bootstrap",
                    "call_type": "branch_quest",
                    "mode": "write",
                },
                {
                    "step": 1,
                    "phase": "act",
                    "call_type": "notify_turn",
                    "mode": "write",
                },
            ],
        }
    )

    report = row["orchestration_report"]
    assert report["orchestration_owner"] == "ARC Harness"
    assert report["status"] == "ok"
    assert report["violations"] == []
    assert report["phase_owner"]["solve"] == "orchestrator"


def test_orchestration_report_flags_phase_violations():
    harness = _make_stub_harness()
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})

    row = runner._submission_row_from_result(
        {
            "task_id": "arc_eval_001",
            "game_id": "game-1",
            "steps": 6, # A042: Need > 5 steps to avoid small-sample suppression
            "correct": False,
            "runtime_seconds": 1.0,
            "final_state": "NOT_FINISHED",
            "final_observation": {"grid": [[1]]},
            "debug_steps": [{"step": i} for i in range(1, 7)],
            "sidequests_ledger": [
                {
                    "step": 3,
                    "phase": "hypothesize",
                    "call_type": "notify_turn",
                    "mode": "write",
                }
            ],
        }
    )

    report = row["orchestration_report"]
    assert report["status"] == "violation"
    assert len(report["violations"]) == 1
    assert report["violations"][0]["type"] == "phase_violation"


def test_orchestration_report_suppresses_violations_on_small_sample():
    """A042: Ensure violations are suppressed when step count is <= 5."""
    harness = _make_stub_harness()
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})

    row = runner._submission_row_from_result(
        {
            "task_id": "arc_eval_001",
            "game_id": "game-1",
            "steps": 3,
            "correct": False,
            "runtime_seconds": 1.0,
            "final_state": "NOT_FINISHED",
            "final_observation": {"grid": [[1]]},
            "debug_steps": [{"step": i} for i in range(1, 4)],
            "sidequests_ledger": [
                {
                    "step": 3,
                    "phase": "hypothesize",
                    "call_type": "notify_turn",
                    "mode": "write",
                }
            ],
        }
    )

    report = row["orchestration_report"]
    assert report["status"] == "ok"
    assert report["small_sample_size"] is True
    assert len(report["suppressed_violations"]) >= 1


def test_orchestration_report_suppresses_violations_in_single_action_environment():
    harness = _make_stub_harness()
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})

    row = runner._submission_row_from_result(
        {
            "task_id": "arc_eval_001",
            "game_id": "game-1",
            "steps": 1,
            "correct": False,
            "runtime_seconds": 1.0,
            "final_state": "NOT_FINISHED",
            "final_observation": {"grid": [[1]]},
            "sidequests_ledger": [
                {
                    "step": 3,
                    "phase": "hypothesize",
                    "call_type": "notify_turn",
                    "mode": "write",
                }
            ],
            "debug_steps": [
                {"step": 1, "available_actions": ["ACTION6"]},
            ],
        }
    )

    report = row["orchestration_report"]
    assert report["single_action_environment"] is True
    assert report["status"] == "ok"
    assert report["violations"] == []
    assert len(report["suppressed_violations"]) == 1
    assert report["suppressed_violations"][0]["type"] == "phase_violation"


def test_compute_progress_reward_includes_target_color_gain():
    harness = _make_stub_harness()
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})

    prev_grid = [
        [0, 1],
        [0, 0],
    ]
    next_grid = [
        [0, 1],
        [1, 0],
    ]

    reward, components = runner._compute_progress_reward(
        env_reward=0.0,
        prev_grid=prev_grid,
        next_grid=next_grid,
        prev_levels_completed=0,
        next_levels_completed=0,
        prev_score=None,
        next_score=None,
        target_color_id=1,
    )
    assert reward > 0.0
    assert components.get("target_color_progress", 0.0) > 0.0


def test_compute_progress_reward_preserves_terminal_env_reward():
    harness = _make_stub_harness()
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})

    reward, components = runner._compute_progress_reward(
        env_reward=1.0,
        prev_grid=[[0]],
        next_grid=[[0]],
        prev_levels_completed=0,
        next_levels_completed=0,
        prev_score=0.0,
        next_score=0.0,
        target_color_id=None,
    )
    assert reward == 1.0


@pytest.mark.asyncio
async def test_steps_preserved_into_submission_row(tmp_path):
    """Regression: ensure ABTaskResult.steps is preserved into the final submission row."""
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    tasks = _sample_tasks()[:1]
    harness = _make_stub_harness()
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})

    result = ABTaskResult(
        task_id="task-1",
        variant=ABVariant.SIDEQUESTS,
        correct=True,
        steps=5,
        tokens_input=1,
        tokens_output=1,
        final_state="WIN",
        final_observation={"grid": [[1]]},
    )

    runner._run_puzzle = AsyncMock(return_value=(result, 0.1))

    rows = await runner.run(tasks, "card-steps")
    assert len(rows) == 1
    assert rows[0]["steps"] == 5
    assert rows[0]["metadata"]["steps"] == 5


def test_extract_env_reward_prefers_explicit_reward_field():
    harness = _make_stub_harness()
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})
    frame = {"state": "NOT_FINISHED", "reward": 0.125}
    assert runner._extract_env_reward(frame) == 0.125


def test_extract_env_reward_falls_back_to_win_terminal():
    harness = _make_stub_harness()
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})
    assert runner._extract_env_reward({"state": "WIN"}) == 1.0
    assert runner._extract_env_reward({"state": "GAME_OVER"}) == 0.0


def test_should_replan_respects_min_interval_without_decline():
    harness = _make_stub_harness()
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})
    runner._last_replan_step = 10
    orchestrator = MagicMock()
    orchestrator._step_history = [
        {"progress_reward": 0.05},
        {"progress_reward": 0.05},
        {"progress_reward": 0.05},
        {"progress_reward": 0.05},
    ]
    orchestrator._hypothesis_context = {"loop_detected": True}
    orchestrator._consecutive_no_progress_steps = 10

    # Within 5-step interval: should not replan unless reward is declining.
    orchestrator._step_history.extend([{"progress_reward": 0.05}] * 3)  # step count = 7
    assert runner._should_replan(orchestrator, no_progress_steps=10) is False


def test_should_replan_allows_early_replan_when_progress_declines():
    harness = _make_stub_harness()
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})
    runner._last_replan_step = 10
    orchestrator = MagicMock()
    # 8-step window: first 4 high, last 4 low -> declining.
    orchestrator._step_history = [
        {"progress_reward": 0.08},
        {"progress_reward": 0.07},
        {"progress_reward": 0.08},
        {"progress_reward": 0.07},
        {"progress_reward": 0.005},
        {"progress_reward": 0.004},
        {"progress_reward": 0.006},
        {"progress_reward": 0.005},
    ]
    orchestrator._hypothesis_context = {"loop_detected": False}
    orchestrator._consecutive_no_progress_steps = 3

    # current_step = 8, since_last_replan = -2 if last=10; emulate realistic count.
    runner._last_replan_step = 5
    assert runner._should_replan(orchestrator, no_progress_steps=3) is True


# ── B89: Benchmark Metrics ──────────────────────────────────────────────


def test_submission_row_includes_benchmark_metrics():
    """B89: _submission_row_from_result should include benchmark_metrics in metadata."""
    harness = _make_stub_harness()
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})

    result = {
        "task_id": "task-1",
        "game_id": "game-1",
        "steps": 5,
        "correct": True,
        "tokens_input": 1000,
        "tokens_output": 200,
        "runtime_seconds": 10.5,
        "final_state": "WIN",
        "final_observation": {"grid": [[0, 1]]},
        "benchmark_metrics": {
            "prompt_budget": {
                "total_steps": 5,
                "avg_tokens_per_step": 120.0,
                "max_tokens_per_step": 150,
                "min_tokens_per_step": 100,
                "first_prompt_detail_level": "rich",
                "asked_for_decision_from_effects": True,
                "invalid_action_count": 0,
                "no_progress_step_count": 1,
            },
            "retrieval_budget": {
                "retrieval_count": 1,
                "total_retrieval_size_bytes": 1500,
                "avg_retrieval_size_bytes": 1500,
            },
        },
    }

    row = runner._submission_row_from_result(result)
    assert "metadata" in row
    assert "benchmark_metrics" in row["metadata"]
    assert row["metadata"]["benchmark_metrics"]["prompt_budget"]["avg_tokens_per_step"] == 120.0
    assert row["metadata"]["benchmark_metrics"]["retrieval_budget"]["retrieval_count"] == 1


def test_prompt_budget_comparison_report_distinguishes_first_input_shapes():
    from benchmarks.arc3.model_eval import build_arc_prompt_budget_comparison_report

    baseline_row = {
        "metadata": {
            "tokens_input": 1200,
            "runtime_seconds": 18.0,
            "steps": 10,
            "benchmark_metrics": {
                "prompt_budget": {
                    "first_prompt_detail_level": "compact",
                    "asked_for_decision_from_effects": False,
                    "invalid_action_count": 3,
                    "no_progress_step_count": 6,
                },
                "retrieval_budget": {
                    "retrieval_count": 2,
                    "total_retrieval_size_bytes": 4200,
                    "avg_retrieval_size_bytes": 2100,
                },
            },
        },
        "prompt_trace": [
            {
                "prompt": "SYSTEM: You are an ARC puzzle solver. Available actions: ACTION1, ACTION2.",
            }
        ],
    }
    candidate_row = {
        "metadata": {
            "tokens_input": 900,
            "runtime_seconds": 14.5,
            "steps": 10,
            "benchmark_metrics": {
                "prompt_budget": {
                    "first_prompt_detail_level": "rich",
                    "asked_for_decision_from_effects": True,
                    "invalid_action_count": 1,
                    "no_progress_step_count": 3,
                },
                "retrieval_budget": {
                    "retrieval_count": 1,
                    "total_retrieval_size_bytes": 1800,
                    "avg_retrieval_size_bytes": 1800,
                },
            },
        },
        "prompt_trace": [
            {
                "prompt": "MEMORY:\nrich prompt\nACTION FACTS:\nreason from observed effects",
            }
        ],
    }

    report = build_arc_prompt_budget_comparison_report(baseline_row, candidate_row)

    assert report["comparison_label"] == "compact_to_rich"
    assert report["baseline"]["prompt_budget"]["first_prompt_detail_level"] == "compact"
    assert report["candidate"]["prompt_budget"]["first_prompt_detail_level"] == "rich"
    assert report["baseline"]["prompt_budget"]["asked_for_decision_from_effects"] is False
    assert report["candidate"]["prompt_budget"]["asked_for_decision_from_effects"] is True
    assert report["delta"]["tokens_input"] == -300
    assert report["delta"]["retrieval_count"] == -1
@pytest.mark.asyncio
async def test_meta_harness_runner_evaluates_candidate(tmp_path):
    from benchmarks.arc3.model_eval import MetaHarnessRunner, HarnessCandidate
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    
    tasks = _sample_tasks()
    brain = NoOpBrainClient()
    
    # Mock runner factory
    def runner_factory(config_patch):
        harness = _make_stub_harness()
        runner = DurableARCRunner(harness, brain, config={"llm": {"model": "test"}})
        # Mock run to return results
        runner.run = AsyncMock(return_value=[
            {"task_id": "task-1", "correct": True, "tokens_input": 100, "steps": 5},
            {"task_id": "task-2", "correct": False, "tokens_input": 200, "steps": 10, "final_state": "LOOP"},
        ])
        return runner

    meta_runner = MetaHarnessRunner(runner_factory, brain)
    candidate = HarnessCandidate(candidate_id="v2", mutation_description="test mutation")
    
    eval_run = await meta_runner.evaluate_candidate(candidate, tasks)
    
    assert eval_run.candidate_id == "v2"
    assert eval_run.solve_rate == 50.0
    assert eval_run.avg_tokens_per_step == (300 / 15)
    assert "LOOP" in eval_run.failure_clusters
    assert eval_run.failure_clusters["LOOP"] == ["task-2"]


@pytest.mark.asyncio
async def test_ingest_api_knowledge_uses_caching():
    """B108: verify that API knowledge ingestion uses the precomputed cache."""
    brain = AsyncMock()
    # We don't need to mock return value because notify_turn is fire-and-forget in adapter,
    # but here we are calling it directly on the mock.
    brain.notify_turn.return_value = {"status": "ok"}
    
    from agents.arc3.api_knowledge import ingest_api_knowledge, API_KNOWLEDGE_CHUNKS
    
    count = await ingest_api_knowledge(brain, "session-123")
    
    assert count == len(API_KNOWLEDGE_CHUNKS)
    assert brain.notify_turn.call_count == len(API_KNOWLEDGE_CHUNKS)
    
    # Check first call has precomputed data
    args, kwargs = brain.notify_turn.call_args_list[0]
    assert "precomputed" in kwargs
    assert kwargs["precomputed"] is not None
    assert "entities" in kwargs["precomputed"]


@pytest.mark.asyncio
async def test_entity_gate_in_bootstrap_write_trace():
    """B121: Entity gate event appears in bootstrap_write_trace."""
    harness = _make_stub_harness()
    # Ensure it finishes immediately
    harness._get_mock_initial_frame.return_value = {
        "frame": [[1, 0, 2]], "available_actions": ["ACTION1"], "state": "WIN", "guid": "g1"
    }
    
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})
    task = ABTask(task_id="t-gate", category="c", prompt="p")
    setattr(task, "game_id", "g1")
    tasks = [task]
    
    with patch("agents.arc3.runner.CheckpointManager") as mock_mgr_cls:
        mock_mgr = mock_mgr_cls.return_value
        mock_checkpoint = MagicMock()
        mock_checkpoint.tasks = {}
        mock_mgr.load_or_create.return_value = mock_checkpoint
        
        results = await runner.run(tasks, "card-gate")
        assert len(results) == 1
        trace = results[0].get("bootstrap_write_trace", [])
        gate_events = [e for e in trace if e["kind"] == "entity_gate"]
        assert len(gate_events) == 1
        assert gate_events[0]["status"] == "ok"

@pytest.mark.asyncio
async def test_entity_gate_in_orchestration_report():
    """B121: orchestration_report includes entity_gate_status."""
    harness = _make_stub_harness()
    harness._get_mock_initial_frame.return_value = {
        "frame": [[1, 0, 2]], "available_actions": ["ACTION1"], "state": "WIN", "guid": "g1"
    }
    
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})
    task = ABTask(task_id="t-report", category="c", prompt="p")
    setattr(task, "game_id", "g1")
    tasks = [task]
    
    with patch("agents.arc3.runner.CheckpointManager") as mock_mgr_cls:
        mock_mgr = mock_mgr_cls.return_value
        mock_checkpoint = MagicMock()
        mock_checkpoint.tasks = {}
        mock_mgr.load_or_create.return_value = mock_checkpoint
        
        results = await runner.run(tasks, "card-report")
        assert len(results) == 1
        report = results[0].get("orchestration_report", {})
        assert "entity_gate_status" in report
        assert report["entity_gate_status"]["status"] == "pass"


@pytest.mark.asyncio
async def test_action_effect_guaranteed_on_win():
    """A053: Verify that perceive_step_response is called even on terminal WIN."""
    harness = _make_stub_harness()
    # First step doesn't win, second step wins
    harness._get_mock_initial_frame.return_value = {
        "frame": [[0]], "available_actions": ["ACTION1"], "state": "NOT_FINISHED", "guid": "g1"
    }
    
    # Mock executor to win on second call
    async def mock_execute(*args, **kwargs):
        return {"frame": [[1]], "state": "WIN"}, 1.0, True, "g1"

    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})
    runner._execute_action = AsyncMock(side_effect=mock_execute)
    
    task = ABTask(task_id="t-win", category="c", prompt="p")
    setattr(task, "game_id", "g1")
    
    with patch("agents.arc3.runner.CheckpointManager") as mock_mgr_cls:
        mock_mgr = mock_mgr_cls.return_value
        mock_checkpoint = MagicMock()
        mock_checkpoint.tasks = {}
        mock_mgr.load_or_create.return_value = mock_checkpoint
        
        with patch("agents.arc3.orchestrator.ARCOrchestrator.perceive_step_response", new_callable=AsyncMock) as mock_perceive:
            await runner.run([task], "card-win")
            
            # Should have been called for the winning step
            # Note: total_steps increments before the call, so it should be called with step=1
            mock_perceive.assert_awaited()
            found_win_call = any(
                call.kwargs.get("done") is True or call.args[3] is True # done is 4th arg or kwarg
                for call in mock_perceive.call_args_list
            )
            assert found_win_call is True


@pytest.mark.asyncio
async def test_upsert_lesson_round_trip():
    """B214: upsert_lesson must persist; recall_relevant_lessons must find it."""
    from mcp_engine.config import load_config
    from mcp_engine.schema import init_schema
    from mcp_engine.graph.kuzu_client import KuzuClient
    from mcp_engine.graph import embeddings as emb
    from mcp_engine.tools import upsert_lesson, recall_relevant_lessons
    try:
        import campy as memory_package
        seed_path = Path(memory_package.__file__).resolve().parent / "data" / "GistSeedExamples.md"
    except Exception:
        try:
            import sidequests as memory_package
            seed_path = Path(memory_package.__file__).resolve().parent / "data" / "GistSeedExamples.md"
        except Exception:
            import mcp_engine
            repo_root = Path(mcp_engine.__file__).resolve().parents[1]
            candidates = [
                repo_root / "campy" / "data" / "GistSeedExamples.md",
                Path.cwd().parent / "sidequests-brain" / "campy" / "data" / "GistSeedExamples.md",
                Path.cwd().parent / "hippocampy" / "campy" / "data" / "GistSeedExamples.md",
            ]
            seed_path = next((path for path in candidates if path.exists()), candidates[0])

    SEED_PATH = str(seed_path)

    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "b214_test.db")
    try:
        config = load_config(None)
        embedding_model = config.get("embeddings", {}).get(
            "model", "sentence-transformers/all-MiniLM-L6-v2"
        )
        emb.configure(config)
        emb.prewarm(embedding_model)
        db = KuzuClient(db_path)
        init_schema(db, SEED_PATH, embedding_model)

        # --- Write ---
        result = await upsert_lesson(
            {
                "text": "space archetype: ACTION6 moves player one cell left",
                "domain": "space",
                "lesson_type": "action_effect",
                "session_id": "test-b214",
            },
            db,
            config,
        )
        assert result.get("lesson_id") is not None, (
            f"upsert_lesson returned lesson_id=None; result={result}"
        )
        assert result.get("status") == "upserted"

        # --- Read back ---
        recall = await recall_relevant_lessons(
            {"query": "space archetype action effect", "domain": "space", "limit": 5},
            db,
            config,
        )
        lessons = recall.get("lessons", [])
        assert len(lessons) >= 1, (
            f"recall_relevant_lessons returned 0 lessons after upsert; recall={recall}"
        )

        # --- Second write (update path) ---
        existing_id = result["lesson_id"]
        result2 = await upsert_lesson(
            {
                "text": "space archetype: ACTION6 moves player one cell left (revised)",
                "domain": "space",
                "lesson_type": "action_effect",
                "lesson_id": existing_id,
                "session_id": "test-b214",
            },
            db,
            config,
        )
        assert result2.get("lesson_id") == existing_id
        assert result2.get("status") == "upserted"

    finally:
        shutil.rmtree(tmp, ignore_errors=True)
