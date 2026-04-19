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
    orch._step_history = []
    orch._last_coverage_snapshot_step = None
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


def test_coverage_snapshot_fires_once_per_step():
    """A025: coverage snapshot must fire exactly once per distinct step number.

    Simulate PERCEIVE re-entry on the same step and ensure only one snapshot
    is emitted per distinct step.
    """
    orch = _make_orchestrator()
    orch._hypothesis_context = {
        "action_coverage": {"untested_actions": ["ACTION2", "ACTION3"]},
    }
    orch._available_actions = ["ACTION1", "ACTION2", "ACTION3"]

    emitted = []
    def _collector(event_type, op, details, result=None, elapsed=None):
        if op == "exploration_coverage_snapshot":
            emitted.append({"op": op, "step": (details or {}).get("step")})
    orch._emit_trace_event = _collector

    # Simulate 3 distinct steps, with the first step re-entering PERCEIVE twice
    orch._step_history = ["s0"]
    orch._emit_coverage_snapshot()
    orch._step_history = ["s0"]
    orch._emit_coverage_snapshot()  # re-entry — should be skipped
    orch._step_history = ["s0", "s1"]
    orch._emit_coverage_snapshot()
    orch._step_history = ["s0", "s1", "s2"]
    orch._emit_coverage_snapshot()

    steps = [e["step"] for e in emitted]
    assert steps == [1, 2, 3], f"expected one emit per distinct step, got {steps}"
