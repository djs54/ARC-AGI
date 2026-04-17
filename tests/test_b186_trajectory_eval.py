import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from agents.arc3.runner import DurableARCRunner
from benchmarks.arc3.trajectory_eval import TrajectoryEvaluator, main as trajectory_eval_main


ALL_ACTIONS = ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5", "ACTION6"]


def _step(
    step_num: int,
    action_id: str,
    *,
    frame_hash: str,
    archetype: str = "space",
    victory: str = "unknown",
    estimated_actions: list[str] | None = None,
):
    return {
        "step": step_num,
        "action_id": action_id,
        "available_actions": list(ALL_ACTIONS),
        "frame_hash": frame_hash,
        "solve_context": {
            "archetype": archetype,
            "victory_condition": {"type": victory} if victory is not None else None,
            "active_chunk": (
                {"estimated_actions": list(estimated_actions)} if estimated_actions is not None else None
            ),
        },
    }


def test_action_diversity_score_extremes():
    evaluator = TrajectoryEvaluator()

    repeated_action_steps = [
        _step(1, "ACTION1", frame_hash="a1"),
        _step(2, "ACTION1", frame_hash="a2"),
        _step(3, "ACTION1", frame_hash="a3"),
    ]
    full_coverage_steps = [
        _step(idx + 1, action, frame_hash=f"f{idx}")
        for idx, action in enumerate(ALL_ACTIONS)
    ]

    repeated_score, _ = evaluator._score_action_diversity(repeated_action_steps)
    full_score, _ = evaluator._score_action_diversity(full_coverage_steps)

    assert repeated_score == 0
    assert full_score == 20


def test_hypothesis_convergence_scores_stable_vs_oscillating():
    evaluator = TrajectoryEvaluator()

    stabilizing_history = [
        _step(1, "ACTION1", frame_hash="a1", archetype="unknown", victory="unknown"),
        _step(2, "ACTION2", frame_hash="a2", archetype="space", victory="goal"),
        _step(3, "ACTION3", frame_hash="a3", archetype="space", victory="goal"),
        _step(4, "ACTION4", frame_hash="a4", archetype="space", victory="goal"),
        _step(5, "ACTION5", frame_hash="a5", archetype="space", victory="goal"),
    ]
    oscillating_history = [
        _step(1, "ACTION1", frame_hash="b1", archetype="space", victory="goal"),
        _step(2, "ACTION2", frame_hash="b2", archetype="race", victory="survive"),
        _step(3, "ACTION3", frame_hash="b3", archetype="space", victory="goal"),
        _step(4, "ACTION4", frame_hash="b4", archetype="race", victory="survive"),
        _step(5, "ACTION5", frame_hash="b5", archetype="space", victory="goal"),
        _step(6, "ACTION6", frame_hash="b6", archetype="race", victory="survive"),
    ]

    stable_score, _ = evaluator._score_hypothesis_convergence(stabilizing_history, [])
    oscillating_score, _ = evaluator._score_hypothesis_convergence(oscillating_history, [])

    assert stable_score == 20
    assert oscillating_score == 0


def test_trajectory_score_total_and_plan_adherence():
    evaluator = TrajectoryEvaluator()
    step_history = [
        _step(1, "ACTION1", frame_hash="f1", estimated_actions=["ACTION1"]),
        _step(2, "ACTION2", frame_hash="f2", estimated_actions=["ACTION2", "ACTION3"]),
        _step(3, "ACTION3", frame_hash="f3", estimated_actions=["ACTION3"]),
        _step(4, "ACTION4", frame_hash="f4", estimated_actions=["ACTION4"]),
    ]
    trace = [
        {
            "event_type": "operation",
            "operation": "no_progress_escalation",
            "details": {"steps": 6},
        }
    ]

    score = evaluator.evaluate(trace, step_history)

    assert score.plan_adherence == 20
    assert score.exploration_efficiency == 20
    assert score.total == (
        score.action_diversity
        + score.hypothesis_convergence
        + score.exploration_efficiency
        + score.plan_adherence
        + score.escalation_quality
    )


def test_cli_supports_saved_jsonl_trace(tmp_path, capsys):
    trace_path = tmp_path / "trajectory.jsonl"
    lines = [
        {
            "snapshot_type": "step",
            "step": 1,
            "action_id": "ACTION1",
            "frame_hash": "x1",
            "available_actions": ALL_ACTIONS,
            "solve_phase_summary": {
                "archetype": "space",
                "victory_condition": "goal",
                "active_chunk": {"estimated_actions": ["ACTION1"]},
            },
        },
        {
            "snapshot_type": "step",
            "step": 2,
            "action_id": "ACTION2",
            "frame_hash": "x2",
            "available_actions": ALL_ACTIONS,
            "solve_phase_summary": {
                "archetype": "space",
                "victory_condition": "goal",
                "active_chunk": {"estimated_actions": ["ACTION2"]},
            },
        },
    ]
    trace_path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")

    exit_code = trajectory_eval_main([str(trace_path)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["total"] >= 0
    assert payload["plan_adherence"] == 20


def test_runner_submission_row_includes_trajectory_score():
    harness = SimpleNamespace(mock_api=True)
    runner = DurableARCRunner(harness, MagicMock(), config={"llm": {"model": "test-model"}})

    row = runner._submission_row_from_result(
        {
            "task_id": "task-1",
            "game_id": "game-1",
            "steps": 4,
            "correct": False,
            "tokens_input": 100,
            "tokens_output": 50,
            "runtime_seconds": 3.2,
            "final_state": "NOT_FINISHED",
            "trajectory_score": {
                "action_diversity": 12,
                "hypothesis_convergence": 8,
                "exploration_efficiency": 14,
                "plan_adherence": 10,
                "escalation_quality": 16,
                "total": 60,
                "details": {},
            },
        }
    )

    assert row["trajectory_score"]["total"] == 60
    assert row["metadata"]["trajectory_score"]["total"] == 60
