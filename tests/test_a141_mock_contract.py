from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from benchmarks.ab_harness import ABTask, BenchmarkConfig
from benchmarks.arc3.adapter import NoOpBrainClient
from benchmarks.arc3.harness import ARC3Harness
import run_single_puzzle as rsp


REQUIRED_FRAME_FIELDS = [
    # benchmarks/arc3/interface_contract.md "FrameResponse required fields"
    "game_id",
    "guid",
    "frame",
    "state",
    "levels_completed",
    "win_levels",
    "action_input",
    "available_actions",
]


def _harness() -> ARC3Harness:
    config = BenchmarkConfig(name="a141", parameters={"max_attempts_per_puzzle": 8})
    return ARC3Harness(config, db=None, mock_api=True)


def _assert_frame_contract(frame: dict) -> None:
    for field in REQUIRED_FRAME_FIELDS:
        assert field in frame


def _assert_grid_contract(grid: list[list[int]]) -> None:
    assert len(grid) == 64
    for row in grid:
        assert len(row) == 64
        for cell in row:
            assert isinstance(cell, int)
            assert 0 <= cell <= 15


def test_initial_frame_has_required_fields():
    harness = _harness()
    frame = harness._get_mock_initial_frame("mock-1")

    _assert_frame_contract(frame)


def test_initial_frame_grid_is_64x64_int_0_15():
    harness = _harness()
    frame = harness._get_mock_initial_frame("mock-1")

    grid = frame["frame"][0]
    _assert_grid_contract(grid)


def test_action_frames_have_required_fields():
    harness = _harness()
    for action in (
        {"action_id": "ACTION1"},
        {"action_id": "ACTION2"},
        {"action_id": "ACTION3"},
        {"action_id": "ACTION4"},
        {"action_id": "ACTION5"},
        {"action_id": "ACTION6", "x": 21, "y": 41},
    ):
        harness._get_mock_initial_frame("mock-1")
        frame, _reward, _done = harness._execute_mock_action("mock-1", action, 0)
        _assert_frame_contract(frame)


def test_action1_changes_grid_action3_does_not():
    harness = _harness()
    initial = harness._get_mock_initial_frame("mock-1")
    grid0 = initial["frame"][0]

    frame1, _reward1, _done1 = harness._execute_mock_action("mock-1", {"action_id": "ACTION1"}, 0)
    grid1 = frame1["frame"][0]

    harness._get_mock_initial_frame("mock-1")
    frame3, _reward3, _done3 = harness._execute_mock_action("mock-1", {"action_id": "ACTION3"}, 0)
    grid3 = frame3["frame"][0]

    assert grid1 != grid0
    assert grid3 == grid0


def test_click_block_c_gains_level():
    harness = _harness()
    harness._get_mock_initial_frame("mock-1")

    frame, reward, done = harness._execute_mock_action("mock-1", {"action_id": "ACTION6", "x": 21, "y": 41}, 0)

    assert frame["levels_completed"] == 1
    assert frame["state"] == "NOT_FINISHED"
    assert reward == 1.0
    assert done is False


def test_two_clicks_win():
    harness = _harness()
    harness._get_mock_initial_frame("mock-1")

    harness._execute_mock_action("mock-1", {"action_id": "ACTION6", "x": 21, "y": 41}, 0)
    frame, reward, done = harness._execute_mock_action("mock-1", {"action_id": "ACTION6", "x": 21, "y": 41}, 1)

    assert frame["levels_completed"] == frame["win_levels"] == 2
    assert frame["state"] == "WIN"
    assert reward == 1.0
    assert done is True


def test_no_auto_win_after_5_steps():
    harness = _harness()
    harness._get_mock_initial_frame("mock-1")

    frame = None
    for step in range(6):
        frame, _reward, done = harness._execute_mock_action("mock-1", {"action_id": "ACTION3"}, step)
        assert done is False

    assert frame is not None
    assert frame["state"] == "NOT_FINISHED"
    assert frame["levels_completed"] == 0


def test_v2_mock_run_emits_candidates_falsification_and_level_gain(tmp_path, monkeypatch):
    # Force a local no-network LLM stub for this integration check.
    monkeypatch.setattr(rsp, "create_llm_client", lambda _config: SimpleNamespace(chat=lambda *_args, **_kwargs: "{}"))

    harness = _harness()
    task = ABTask(task_id="a141-v2", category="mock", prompt="mock")
    setattr(task, "game_id", "mock-a141")

    runner = object.__new__(rsp.SingleTaskRunner)
    runner.harness = harness
    runner.config = {"benchmark": {"max_attempts_per_puzzle": 12}, "llm": {"provider": "mock"}}
    runner.real_api = False
    runner.world_model_eval = False
    runner.live_output_path = tmp_path / "submission_results_single.live.jsonl"
    runner.append_live_snapshot = lambda snapshot: rsp.SingleTaskRunner.append_live_snapshot(runner, snapshot)

    _result = rsp._run_arc_v2_task(task, runner, "card-a141", NoOpBrainClient(), args=None)

    assert runner.live_output_path.exists()
    rows = [json.loads(line) for line in runner.live_output_path.read_text().splitlines() if line.strip()]
    step_rows = [row for row in rows if row.get("snapshot_type") == "step"]

    assert step_rows
    assert any(int(row.get("planner_candidate_count") or 0) > 1 for row in step_rows)
    assert any(int(row.get("falsification_delta") or 0) > 0 for row in step_rows)
    assert any(str(row.get("progress_class") or "") == "level" for row in step_rows)
