"""Unit tests for B166 deterministic autopilot."""

from __future__ import annotations

from unittest.mock import MagicMock

from benchmarks.arc3.state_serializer import StateSerializerForARC
from agents.arc3.orchestrator import ARCOrchestrator


def _make_orchestrator():
    brain = MagicMock()
    return ARCOrchestrator(brain_client=brain, llm_client=None, session_id="s1", serializer=StateSerializerForARC(), config={})


def test_autopilot_moves_up_when_goal_above():
    orch = _make_orchestrator()
    orch._solve_context = {"object_roles": {
        "14": {"role": "player", "confidence": 0.8, "estimated_position": {"row": 50.0, "col": 33.0}},
        "2": {"role": "goal", "confidence": 0.9, "estimated_position": {"row": 29.0, "col": 33.0}},
    }}
    res = orch._try_autopilot({}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"])
    assert res is not None
    assert res["action_id"] == "ACTION1"
    assert res["decision_source"] == "autopilot"


def test_autopilot_moves_right_when_goal_right():
    orch = _make_orchestrator()
    orch._solve_context = {"object_roles": {
        "14": {"role": "player", "confidence": 0.8, "estimated_position": {"row": 32.0, "col": 20.0}},
        "5": {"role": "goal", "confidence": 0.9, "estimated_position": {"row": 31.0, "col": 45.0}},
    }}
    res = orch._try_autopilot({}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"])
    assert res is not None
    assert res["action_id"] == "ACTION4"


def test_autopilot_interacts_when_arrived():
    orch = _make_orchestrator()
    orch._solve_context = {"object_roles": {
        "14": {"role": "player", "confidence": 0.8, "estimated_position": {"row": 31.0, "col": 28.0}},
        "5": {"role": "goal", "confidence": 0.9, "estimated_position": {"row": 31.0, "col": 28.5}},
    }}
    res = orch._try_autopilot({}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"])
    assert res is not None
    assert res["action_id"] == "ACTION5"


def test_autopilot_returns_none_when_low_confidence():
    orch = _make_orchestrator()
    orch._solve_context = {"object_roles": {
        "14": {"role": "player", "confidence": 0.4, "estimated_position": {"row": 50.0, "col": 33.0}},
        "2": {"role": "goal", "confidence": 0.9, "estimated_position": {"row": 29.0, "col": 33.0}},
    }}
    res = orch._try_autopilot({}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"])
    assert res is None


def test_autopilot_returns_none_when_no_positions():
    orch = _make_orchestrator()
    orch._solve_context = {"object_roles": {
        "14": {"role": "player", "confidence": 0.8, "estimated_position": None},
        "2": {"role": "goal", "confidence": 0.9, "estimated_position": {"row": 29.0, "col": 33.0}},
    }}
    res = orch._try_autopilot({}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"])
    assert res is None


def test_autopilot_disengages_on_wall_collision():
    orch = _make_orchestrator()
    orch._solve_context = {"object_roles": {
        "14": {"role": "player", "confidence": 0.8, "estimated_position": {"row": 50.0, "col": 33.0}},
        "2": {"role": "goal", "confidence": 0.9, "estimated_position": {"row": 29.0, "col": 33.0}},
    }}
    orch._step_history = [
        {"decision_source": "autopilot", "frame_delta": {"n_cells_changed": 0}},
        {"decision_source": "autopilot", "frame_delta": {"n_cells_changed": 0}},
    ]
    res = orch._try_autopilot({}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"])
    assert res is None


def test_autopilot_prioritizes_larger_delta():
    orch = _make_orchestrator()
    orch._solve_context = {"object_roles": {
        "14": {"role": "player", "confidence": 0.8, "estimated_position": {"row": 50.0, "col": 30.0}},
        "2": {"role": "goal", "confidence": 0.9, "estimated_position": {"row": 30.0, "col": 33.0}},
    }}
    res = orch._try_autopilot({}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"])
    assert res is not None
    assert res["action_id"] == "ACTION1"
