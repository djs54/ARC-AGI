
import pytest
from unittest.mock import MagicMock
from agents.arc3.runner import DurableARCRunner
from agents.arc3.phase import SolvePhase
from agents.arc3.solver import ObjectRole, RoleType

class MockTask:
    def __init__(self, task_id):
        self.task_id = task_id
        self.game_id = "test-game"
        self.reference_solution = None

def test_replan_branch_exploration_incomplete():
    harness = MagicMock()
    brain = MagicMock()
    runner = DurableARCRunner(harness, brain, {})
    
    orchestrator = MagicMock()
    orchestrator._solve_context = {
        "active_chunk": {"source": "explore"},
        "archetype": "space",
        "victory_condition": {"type": "reach_goal"}
    }
    orchestrator._hypothesis_context = {
        "action_coverage": {
            "initial_exploration_complete": False,
            "tested_count": 1,
            "available_total": 4
        }
    }
    orchestrator.solve_engine = MagicMock(_archetype_confidence=0.5)
    
    target = runner._replan_target(orchestrator)
    assert target == SolvePhase.MODEL
    # Verify trace reason (optional but good)
    orchestrator._emit_trace_event.assert_called_with("replan_exit", "route", {"target": "model", "route_reason": "exploration_incomplete"})

def test_replan_branch_all_low_value_high_geometry():
    harness = MagicMock()
    brain = MagicMock()
    runner = DurableARCRunner(harness, brain, {})
    
    orchestrator = MagicMock()
    # High geometry confidence
    player = ObjectRole(color_id=3, role=RoleType.PLAYER, confidence=0.9, estimated_position={"row": 1, "col": 1})
    goal = ObjectRole(color_id=11, role=RoleType.GOAL, confidence=0.9, estimated_position={"row": 5, "col": 5})
    
    orchestrator._solve_context = {
        "active_chunk": {"source": "explore"},
        "archetype": "space",
        "victory_condition": {"type": "reach_goal"},
        "object_roles": {3: player, 11: goal}
    }
    # All actions tested and low value
    orchestrator._hypothesis_context = {
        "action_coverage": {
            "initial_exploration_complete": True,
            "tested_count": 1,
            "available_total": 1,
            "untested_count": 0
        },
        "action_facts": [
            {"action": "ACTION1", "fact_type": "deterministic_effect", "value_status": "low_value"}
        ]
    }
    orchestrator.solve_engine = MagicMock(_archetype_confidence=0.5)
    
    target = runner._replan_target(orchestrator)
    assert target == SolvePhase.MODEL
    orchestrator._emit_trace_event.assert_called_with("replan_exit", "route", {"target": "model", "route_reason": "low_value_but_known_geometry"})

def test_replan_branch_signature_escalation():
    harness = MagicMock()
    brain = MagicMock()
    runner = DurableARCRunner(harness, brain, {})
    
    orchestrator = MagicMock()
    orchestrator._solve_context = {
        "active_chunk": {"source": "explore"},
        "archetype": "space",
        "victory_condition": {"type": "reach_goal"}
    }
    orchestrator._hypothesis_context = {
        "action_coverage": {"initial_exploration_complete": True, "untested_count": 0}
    }
    orchestrator.solve_engine = MagicMock(_archetype_confidence=0.5)
    
    # First time -> ROUTE
    runner._replan_target(orchestrator)
    
    # Second time with same signature -> MODEL (escalation)
    target = runner._replan_target(orchestrator)
    assert target == SolvePhase.MODEL
    orchestrator._emit_trace_event.assert_called_with("replan_exit", "route", {"target": "model", "route_reason": "signature_escalation"})

def test_replan_branch_low_archetype_conf():
    harness = MagicMock()
    brain = MagicMock()
    runner = DurableARCRunner(harness, brain, {})
    
    orchestrator = MagicMock()
    orchestrator._solve_context = {
        "active_chunk": {"source": "explore"},
        "archetype": "space",
        "victory_condition": {"type": "reach_goal"}
    }
    orchestrator._hypothesis_context = {
        "action_coverage": {"initial_exploration_complete": True, "untested_count": 0}
    }
    orchestrator.solve_engine = MagicMock(_archetype_confidence=0.1) # Low conf
    
    target = runner._replan_target(orchestrator)
    assert target == SolvePhase.HYPOTHESIZE
    orchestrator._emit_trace_event.assert_called_with("replan_exit", "route", {"target": "hypothesize", "route_reason": "low_archetype_conf"})

def test_replan_branch_default_route():
    harness = MagicMock()
    brain = MagicMock()
    runner = DurableARCRunner(harness, brain, {})
    
    orchestrator = MagicMock()
    orchestrator._solve_context = {
        "active_chunk": {"source": "explore"},
        "archetype": "space",
        "victory_condition": {"type": "reach_goal"}
    }
    orchestrator._hypothesis_context = {
        "action_coverage": {"initial_exploration_complete": True, "untested_count": 0}
    }
    orchestrator.solve_engine = MagicMock(_archetype_confidence=0.8)
    
    target = runner._replan_target(orchestrator)
    assert target == SolvePhase.ROUTE
    orchestrator._emit_trace_event.assert_called_with("replan_exit", "route", {"target": "route", "route_reason": "rebuild_route_from_saturation"})
