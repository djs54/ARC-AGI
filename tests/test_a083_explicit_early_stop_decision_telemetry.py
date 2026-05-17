import json
from types import SimpleNamespace

from benchmarks.arc3.world_model_eval import WorldModelEvaluator
from run_single_puzzle import SingleTaskRunner


def test_world_model_decision_row_tracks_pre_action_early_stop():
    evaluator = WorldModelEvaluator()

    row = evaluator.build_decision_row(
        "task-1",
        {
            "snapshot_type": "world_model_decision",
            "decision": "early_stop",
            "trigger": "single_action_terminal_stall",
            "executed_step_count": 4,
            "decision_step": 5,
            "stall_evidence_count": 5,
            "stall_threshold": 5,
            "action_id": "ACTION6",
            "action_effect_class": "no_op",
            "repeated_frame_hash_count": 4,
            "world_model_node_count": 7,
            "world_model_edge_count": 9,
        },
    )
    summary = evaluator.build_summary_row("task-1", {"world_model_snapshot": {"node_count": 7}})

    assert row.kind == "world_model_decision"
    assert row.executed_step_count == 4
    assert row.decision_step == 5
    assert row.stall_evidence_count == 5
    assert summary.early_stop_decision_count == 1
    assert summary.world_model_decision_count == 1


def test_live_stream_writes_world_model_decision_row(tmp_path):
    runner = SingleTaskRunner.__new__(SingleTaskRunner)
    runner.live_output_path = tmp_path / "live.jsonl"
    runner.world_model_live_output_path = tmp_path / "world_model.live.jsonl"
    runner.world_model_eval = True
    runner.world_model_evaluator = WorldModelEvaluator()

    runner.append_live_snapshot(
        {
            "snapshot_type": "world_model_decision",
            "task_id": "task-1",
            "decision": "early_stop",
            "trigger": "single_action_terminal_stall",
            "executed_step_count": 4,
            "decision_step": 5,
            "stall_evidence_count": 5,
            "stall_threshold": 5,
            "world_model_node_count": 3,
            "world_model_edge_count": 4,
        }
    )

    row = json.loads(runner.world_model_live_output_path.read_text().strip())
    assert row["kind"] == "world_model_decision"
    assert row["decision"] == "early_stop"
    assert row["executed_step_count"] == 4
    assert row["decision_step"] == 5


def test_strategy_exhaustion_decision_row_preserves_graph_failure_fields():
    evaluator = WorldModelEvaluator()

    row = evaluator.build_decision_row(
        "task-1",
        {
            "snapshot_type": "world_model_decision",
            "decision": "early_stop",
            "world_model_decision": "strategy_exhausted",
            "trigger": "all_actions_churn_strategy_exhausted",
            "failure_class": "strategy_exhausted",
            "failure_reason": "all_actions_churn_no_progress",
            "all_actions_churn_evidence": {
                "all_actions_churn": True,
                "total_churn_count": 6,
                "actions_tested_count": 3,
            },
        },
    )
    summary = evaluator.build_summary_row("task-1", {})

    assert row.world_model_decision == "strategy_exhausted"
    assert row.failure_reason == "all_actions_churn_no_progress"
    assert row.all_actions_churn_detected is True
    assert summary.world_model_decision_count == 1
    assert summary.early_stop_decision_count == 1
