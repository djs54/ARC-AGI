"""Tests for A153: world-model live telemetry silently stale when --world-model-eval is off.

Bug: SingleTaskRunner.reset_live_output() only truncated world_model_live_output_path
when world_model_eval was True. With the flag off (the default), a stale prior run's
world-model telemetry was left untouched and silently linked from
run_review.artifact_urls as if it belonged to the current run.

Fix: reset_live_output() always resets the file — real truncation when eval is on,
an explicit JSON "disabled" marker row when eval is off.
"""

import json

from arc_runtime.runner_shell import SingleTaskRunner
from benchmarks.arc3.world_model_eval import WorldModelEvaluator


def _make_runner(tmp_path, world_model_eval: bool) -> SingleTaskRunner:
    runner = SingleTaskRunner.__new__(SingleTaskRunner)
    runner.live_output_path = tmp_path / "submission_results_single.live.jsonl"
    runner.world_model_live_output_path = tmp_path / "submission_results_single.world_model.live.jsonl"
    runner.world_model_eval = world_model_eval
    runner.world_model_evaluator = WorldModelEvaluator()
    return runner


def test_reset_with_eval_enabled_truncates_and_resets_evaluator(tmp_path):
    runner = _make_runner(tmp_path, world_model_eval=True)
    runner.world_model_live_output_path.write_text(
        '{"kind": "world_model_step", "task_id": "stale_task", "step": 99}\n'
    )

    reset_calls = []
    runner.world_model_evaluator.reset = lambda: reset_calls.append(True)

    runner.reset_live_output()

    assert runner.world_model_live_output_path.read_text() == ""
    assert reset_calls == [True]


def test_reset_with_eval_disabled_writes_marker_not_stale_content(tmp_path):
    runner = _make_runner(tmp_path, world_model_eval=False)
    stale_content = '{"kind": "world_model_step", "task_id": "SU15", "step": 7, "failure_class": "crash"}\n'
    runner.world_model_live_output_path.write_text(stale_content)

    runner.reset_live_output()

    content = runner.world_model_live_output_path.read_text()
    lines = content.strip().splitlines()
    assert len(lines) == 1

    row = json.loads(lines[0])
    assert row["world_model_eval"] is False
    assert "SU15" not in content
    assert "crash" not in content
    assert "stale_task" not in content


def test_two_runs_eval_then_disabled_no_bleed_through(tmp_path):
    # Run 1: eval enabled, writes real step rows via the normal append path.
    run1 = _make_runner(tmp_path, world_model_eval=True)
    run1.reset_live_output()
    run1.append_live_snapshot(
        {
            "task_id": "run1_task",
            "step": 3,
            "total_steps": 0,
            "world_model_node_count": 5,
            "world_model_edge_count": 4,
            "compiled_world_delta": {"claims_count": 2, "effect_class": "pixel_churn"},
        }
    )
    run1_content = run1.world_model_live_output_path.read_text()
    assert "run1_task" in run1_content

    # Run 2: same artifacts dir/path, eval disabled — must not see run 1's rows.
    run2 = _make_runner(tmp_path, world_model_eval=False)
    run2.world_model_live_output_path = run1.world_model_live_output_path
    run2.reset_live_output()

    run2_content = run2.world_model_live_output_path.read_text()
    assert "run1_task" not in run2_content

    lines = run2_content.strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["world_model_eval"] is False
