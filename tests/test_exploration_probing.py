import pytest

from agents.arc3.orchestrator import ARCOrchestrator


def _make_orchestrator():
    """Minimal orchestrator with just the attributes the guard touches."""
    orch = ARCOrchestrator.__new__(ARCOrchestrator)
    orch._hypothesis_context = {}
    orch._solve_context = {}
    orch._consecutive_no_progress_steps = 0
    orch._untested_probes_forced_in_run = 0
    orch._blocked_actions = set()
    orch._available_actions = []
    orch._action_frame_hashes = {}
    orch._action_fatigue = {}
    orch.observed_action_effects = {}
    orch._emit_trace_event = lambda *a, **kw: None
    orch.solve_engine = type("_S", (), {"_active_chunk": None})()
    orch._current_level = 2
    orch._rule_confidence = 0.0
    orch._forced_exploration_count = 0
    orch._exploration_budget_multiplier = 1.0
    orch._total_forced_exploration = 0
    return orch


def _action(action_id, source="llm", rationale="pick this"):
    return {
        "action_id": action_id,
        "rationale": rationale,
        "decision_source": source,
    }


def test_guard_fires_on_untested_after_two_no_progress():
    orch = _make_orchestrator()
    orch._hypothesis_context = {
        "action_coverage": {"untested_actions": ["ACTION4", "ACTION6"]},
    }
    orch._consecutive_no_progress_steps = 2
    result = orch._enforce_action_policy(
        _action("ACTION1"),
        available_actions=["ACTION1", "ACTION2", "ACTION4", "ACTION6"],
    )
    assert result["action_id"] == "ACTION4"  # alphabetical first
    assert result["decision_source"] == "policy_untested_probe"
    assert orch._untested_probes_forced_in_run == 1


def test_guard_silent_when_all_tried():
    orch = _make_orchestrator()
    orch._hypothesis_context = {"action_coverage": {"untested_actions": []}}
    orch._consecutive_no_progress_steps = 5
    result = orch._enforce_action_policy(
        _action("ACTION1"),
        available_actions=["ACTION1", "ACTION2"],
    )
    assert result.get("decision_source") != "policy_untested_probe"
    assert result["action_id"] == "ACTION1"
    assert orch._untested_probes_forced_in_run == 0


def test_guard_silent_on_fresh_progress():
    orch = _make_orchestrator()
    orch._hypothesis_context = {
        "action_coverage": {"untested_actions": ["ACTION4"]},
    }
    orch._consecutive_no_progress_steps = 0
    result = orch._enforce_action_policy(
        _action("ACTION1"),
        available_actions=["ACTION1", "ACTION4"],
    )
    assert result["action_id"] == "ACTION1"
    assert orch._untested_probes_forced_in_run == 0


def test_guard_yields_to_autopilot():
    orch = _make_orchestrator()
    orch._hypothesis_context = {
        "action_coverage": {"untested_actions": ["ACTION4", "ACTION6"]},
    }
    orch._consecutive_no_progress_steps = 5
    result = orch._enforce_action_policy(
        _action("ACTION1", source="autopilot"),
        available_actions=["ACTION1", "ACTION4", "ACTION6"],
    )
    assert result["action_id"] == "ACTION1"
    assert orch._untested_probes_forced_in_run == 0
