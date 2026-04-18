import pytest
from agents.arc3.runner import DurableARCRunner
from agents.arc3.phase import SolvePhase
from agents.arc3.solver import RoleType

class _StubSolveEngine:
    def __init__(self, arch_conf=0.8):
        self._archetype_confidence = arch_conf


class _StubOrchestrator:
    def __init__(self, *, solve_ctx=None, hyp_ctx=None, arch_conf=0.8):
        self._solve_context = solve_ctx or {}
        self._hypothesis_context = hyp_ctx or {}
        self.solve_engine = _StubSolveEngine(arch_conf=arch_conf)
        self.session_id = "test-session"
    def _emit_trace_event(self, *args, **kwargs):
        pass


def _runner():
    r = DurableARCRunner.__new__(DurableARCRunner)
    r._last_replan_signature = None
    return r


class _RoleStub:
    def __init__(self, role_val, conf):
        class _Val:
            def __init__(self, v): self.value = v
        self.role = _Val(role_val)
        self.confidence = conf
        self.estimated_position = {"row": 0, "col": 0}


def _high_conf_roles():
    return {
        1: _RoleStub("player", 0.9),
        2: _RoleStub("goal", 0.9)
    }


def test_low_value_but_known_geometry_routes_to_model():
    r = _runner()
    orch = _StubOrchestrator(
        solve_ctx={"object_roles": _high_conf_roles()},
        hyp_ctx={
            "action_facts": [
                {"fact_type": "deterministic_effect", "value_status": "low_value"},
                {"fact_type": "deterministic_effect", "value_status": "low_value"},
            ],
            "action_coverage": {
                "tested_count": 2, "available_total": 2, "untested_count": 0,
                "initial_exploration_complete": True,
            },
        },
    )
    phase, reason = r._replan_target(orch)
    assert phase == SolvePhase.MODEL
    assert reason == "low_value_but_known_geometry"


def test_signature_repeated_escalates_to_model():
    r = _runner()
    orch = _StubOrchestrator(
        solve_ctx={"archetype": "maze", "active_chunk": {"source": "explore"}},
        hyp_ctx={"action_coverage": {"initial_exploration_complete": True,
                                     "tested_count": 1, "available_total": 4,
                                     "untested_count": 3}},
    )
    r._replan_target(orch)   # prime signature
    phase, reason = r._replan_target(orch)
    assert phase == SolvePhase.MODEL
    assert reason == "signature_escalation"


def test_exploration_incomplete_routes_to_model():
    r = _runner()
    orch = _StubOrchestrator(
        hyp_ctx={"action_coverage": {"initial_exploration_complete": False,
                                     "tested_count": 0, "available_total": 4,
                                     "untested_count": 4}},
    )
    phase, reason = r._replan_target(orch)
    assert phase == SolvePhase.MODEL
    assert reason == "exploration_incomplete"


def test_low_archetype_conf_routes_to_hypothesize():
    r = _runner()
    orch = _StubOrchestrator(
        arch_conf=0.1,
        hyp_ctx={"action_coverage": {"initial_exploration_complete": True,
                                     "tested_count": 4, "available_total": 4,
                                     "untested_count": 0}},
    )
    phase, reason = r._replan_target(orch)
    assert phase == SolvePhase.HYPOTHESIZE
    assert reason == "low_archetype_conf"


def test_coverage_saturated_routes_to_route():
    r = _runner()
    orch = _StubOrchestrator(
        arch_conf=0.8,
        solve_ctx={"object_roles": _high_conf_roles()},
        hyp_ctx={
            "action_facts": [
                {"fact_type": "deterministic_effect", "value_status": "medium_value"},
            ],
            "action_coverage": {
                "tested_count": 4, "available_total": 4, "untested_count": 0,
                "initial_exploration_complete": True,
            },
        },
    )
    phase, reason = r._replan_target(orch)
    assert phase == SolvePhase.ROUTE
    assert reason == "rebuild_route_from_saturation"


def test_default_fallthrough_routes_to_route():
    r = _runner()
    orch = _StubOrchestrator(
        arch_conf=0.8,
        hyp_ctx={"action_coverage": {"initial_exploration_complete": True,
                                     "tested_count": 2, "available_total": 4,
                                     "untested_count": 2}},
    )
    phase, reason = r._replan_target(orch)
    assert phase == SolvePhase.ROUTE
    assert reason == "default"
