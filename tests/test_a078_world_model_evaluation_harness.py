import pytest
from benchmarks.arc3.world_model_eval import WorldModelEvaluator
from agents.arc3.solver import TerminalGroundedScore
from run_single_puzzle import SingleTaskRunner, _atomic_dump_json

def test_world_model_evaluator_step_row():
    evaluator = WorldModelEvaluator()
    snapshot = {
        "world_model_node_count": 42,
        "world_model_edge_count": 71,
        "compiled_world_delta": {
            "claims_count": 4,
            "effect_class": "pixel_churn",
            "failure_signal": None
        },
        "reasoning_mode": "cheap_execute"
    }
    
    row = evaluator.build_step_row("task1", 12, snapshot)
    assert row.world_model_node_count == 42
    assert row.compiled_claim_count == 4
    assert row.reasoning_mode == "cheap_execute"
    assert row.single_action_stall_detected is False


def test_world_model_step_metrics_include_executed_action_visibility():
    evaluator = WorldModelEvaluator()

    row = evaluator.build_step_row(
        "task1",
        7,
        {
            "action_id": "ACTION4",
            "decision_source": "cheap_probe",
            "compiled_world_delta": {"claims_count": 1, "effect_class": "harmful"},
            "reasoning_gating": {},
        },
    )

    assert row.action_id == "ACTION4"
    assert row.decision_source == "cheap_probe"
    assert row.action_effect_class == "harmful"

def test_world_model_evaluator_summary_row():
    evaluator = WorldModelEvaluator()
    class MockResult:
        def __init__(self):
            self.world_model_snapshot = {"node_count": 150}
            
    row = evaluator.build_summary_row("task1", MockResult())
    assert row.graph_bounded is True
    assert row.kind == "world_model_summary"
    assert row.to_dict()["kind"] == "world_model_summary"

def test_world_model_evaluator_summary_row_accepts_final_snapshot_dict():
    evaluator = WorldModelEvaluator()
    row = evaluator.build_summary_row("task1", {"world_model_snapshot": {"node_count": 250}})
    assert row.graph_bounded is False
    assert row.kind == "world_model_summary"

def test_world_model_evaluator_summary_carries_step_stall_signal():
    evaluator = WorldModelEvaluator()
    evaluator.build_step_row(
        "task1",
        1,
        {
            "compiled_world_delta": {
                "claims_count": 1,
                "effect_class": "pixel_churn",
                "failure_signal": "single_action_terminal_stall",
            }
        },
    )
    row = evaluator.build_summary_row("task1", {"world_model_snapshot": {"node_count": 5}})
    assert row.single_action_stall_detected is True


def test_atomic_dump_json_serializes_terminal_grounded_score(tmp_path):
    path = tmp_path / "result.json"
    _atomic_dump_json(
        path,
        {
            "terminal_score": TerminalGroundedScore(
                total_score=1.25,
                proximity_delta=0.5,
                reason="fixture",
            )
        },
    )

    assert path.exists()
    assert not path.with_suffix(".json.tmp").exists()
    assert '"terminal_score"' in path.read_text()


def test_world_model_live_stream_uses_step_before_total_steps(tmp_path):
    runner = SingleTaskRunner.__new__(SingleTaskRunner)
    runner.live_output_path = tmp_path / "live.jsonl"
    runner.world_model_live_output_path = tmp_path / "world_model.live.jsonl"
    runner.world_model_eval = True
    runner.world_model_evaluator = WorldModelEvaluator()

    runner.append_live_snapshot(
        {
            "task_id": "task1",
            "step": 12,
            "total_steps": 0,
            "world_model_node_count": 4,
            "world_model_edge_count": 3,
            "compiled_world_delta": {"claims_count": 1, "effect_class": "pixel_churn"},
        }
    )

    row = runner.world_model_live_output_path.read_text().strip()
    assert '"step": 12' in row


def test_world_model_step_row_preserves_terminal_and_churn_evidence():
    evaluator = WorldModelEvaluator()

    row = evaluator.build_step_row(
        "task1",
        8,
        {
            "action_id": "ACTION4",
            "reward": 0.3,
            "progress_reward": 0.3,
            "reward_components": {
                "meaningful_progress": False,
                "progress_class": "local_object_progress",
                "progress_gate_reason": "oscillating_without_terminal_progress",
            },
            "terminal_progress_trend": "oscillating",
            "terminal_goal_distance": 42.5,
            "terminal_value_score": 0.12,
            "compiled_world_delta": {
                "claims_count": 1,
                "effect_class": "object_progress",
                "terminal_alignment": "oscillating",
                "terminal_aligned": False,
            },
            "reasoning_gating": {
                "mode": "multi_action_churn_probe",
                "all_actions_churn_evidence": {
                    "all_actions_churn": True,
                    "total_churn_count": 12,
                    "total_progress_count": 0,
                    "total_local_progress_count": 3,
                    "action_summaries": {
                        "ACTION4": {
                            "tested_count": 2,
                            "churn_count": 1,
                            "local_progress_count": 1,
                            "progress_count": 0,
                        }
                    },
                },
            },
        },
    )

    assert row.terminal_progress_trend == "oscillating"
    assert row.terminal_goal_distance == 42.5
    assert row.terminal_value_score == 0.12
    assert row.terminal_alignment == "oscillating"
    assert row.terminal_aligned is False
    assert row.meaningful_progress is False
    assert row.progress_class == "local_object_progress"
    assert row.progress_gate_reason == "oscillating_without_terminal_progress"
    assert row.all_actions_churn_detected is True
    assert row.all_actions_churn_count == 12
    assert row.total_local_progress_count == 3
    assert row.all_actions_churn_evidence["action_summaries"]["ACTION4"]["local_progress_count"] == 1
