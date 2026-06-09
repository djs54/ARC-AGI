"""Tests for A134 available-actions wiring."""

from benchmarks.arc3.adapter import ARC3Adapter, NoOpBrainClient
from run_single_puzzle import _unwrap_arc_game_payload


def test_unwrap_arc_game_payload_preserves_available_actions():
    raw = {
        "guid": "guid-1",
        "payload": {
            "frame": [[[0, 1], [1, 0]]],
            "state": "NOT_FINISHED",
            "available_actions": ["ACTION1", "ACTION2", "ACTION3"],
            "step_num": 1,
        },
    }

    payload = _unwrap_arc_game_payload(raw)

    assert payload["guid"] == "guid-1"
    assert payload["available_actions"] == ["ACTION1", "ACTION2", "ACTION3"]
    assert payload["frame"] == [[[0, 1], [1, 0]]]


def test_normalized_observation_carries_available_actions_from_payload():
    raw = {
        "guid": "guid-2",
        "payload": {
            "frame": [[[0, 0], [1, 1]]],
            "state": "NOT_FINISHED",
            "available_actions": ["ACTION4", "ACTION5"],
            "step_num": 1,
        },
    }

    payload = _unwrap_arc_game_payload(raw)
    adapter = ARC3Adapter(NoOpBrainClient(), session_id="session-1", task_id="task-1")
    observation = adapter.normalize_observation(payload)

    assert observation["available_actions"] == ["ACTION4", "ACTION5"]
    assert len(observation["available_actions"]) == 2
