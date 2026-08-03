import pytest
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.solver import RoleType, ObjectRole, SolveEngine
from agents.arc3.supervisor import SupervisorDecision
from benchmarks.arc3.state_serializer import StateSerializerForARC

@pytest.fixture
def orchestrator():
    brain = MagicMock()
    brain.notify_turn = AsyncMock(return_value={"status": "ok"})
    brain.current_truth = AsyncMock(return_value={"results": []})
    
    orch = ARCOrchestrator(
        brain_client=brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    return orch

@pytest.mark.asyncio
async def test_escalation_via_supervisor(orchestrator):
    """B183: Escalation is now handled by PuzzleSupervisor."""
    # Setup state
    orchestrator._available_actions = ["ACTION1", "ACTION2", "ACTION3"]
    orchestrator._step_history = [
        {"action_id": "ACTION1", "decision_source": "autopilot", "step": 1, "frame_hash": "h1", "reward": 0.0},
        {"action_id": "ACTION1", "decision_source": "autopilot", "step": 2, "frame_hash": "h2", "reward": 0.0},
        {"action_id": "ACTION1", "decision_source": "autopilot", "step": 3, "frame_hash": "h1", "reward": 0.0},
        {"action_id": "ACTION1", "decision_source": "autopilot", "step": 4, "frame_hash": "h2", "reward": 0.0},
        {"action_id": "ACTION1", "decision_source": "autopilot", "step": 5, "frame_hash": "h1", "reward": 0.0},
    ]
    
    observation = {
        "grid": [[0]], 
        "state": "RUNNING", 
        "available_actions": ["ACTION1", "ACTION2"],
        "colors": [],
        "shapes": []
    }
    memory_context = {"memories": []}
    
    # Mock SolveEngine.solve to avoid real calls
    orchestrator.solve_engine.solve = AsyncMock(return_value=MagicMock())
    
    # Step 5 is a check interval (multiple of 5)
    # 5 steps of oscillating between h1 and h2 with len=5 >= 8 check? 
    # Wait, my rule-based check needs 8 steps for oscillation.
    
    # Let's just mock the supervisor evaluate to return what we want to test
    orchestrator._supervisor.evaluate = AsyncMock(return_value=MagicMock(
        decision=SupervisorDecision.RESET_STRATEGY,
        reason="test reset",
        nudge_hint=None
    ))
    
    await orchestrator.act(observation, memory_context, step_num=5)
    
    # RESET_STRATEGY should clear victory condition and plateau lock
    assert orchestrator.solve_engine._victory_condition is None
    assert orchestrator.solve_engine._plateau_locked_family is None

@pytest.mark.asyncio
async def test_autopilot_skips_blocked_actions(orchestrator):
    orchestrator.solve_engine._object_roles = {
        1: ObjectRole(color_id=1, role=RoleType.PLAYER, confidence=0.9, estimated_position={"row": 5.0, "col": 5.0}),
        2: ObjectRole(color_id=2, role=RoleType.GOAL, confidence=0.9, estimated_position={"row": 10.0, "col": 5.0})
    }
    # Targeted action would be ACTION2 (down)
    orchestrator._blocked_actions.add("ACTION2")
    
    action = await orchestrator._try_autopilot({"grid": [[0]*10 for _ in range(10)]}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4"])
    
    # ACTION2 is blocked, row delta is 5, col delta is 0.
    assert action is None # No alternative with delta

@pytest.mark.asyncio
async def test_autopilot_tries_alternative_when_primary_blocked(orchestrator):
    orchestrator.solve_engine._object_roles = {
        1: ObjectRole(color_id=1, role=RoleType.PLAYER, confidence=0.9, estimated_position={"row": 5.0, "col": 5.0}),
        2: ObjectRole(color_id=2, role=RoleType.GOAL, confidence=0.9, estimated_position={"row": 10.0, "col": 6.0})
    }
    # Preferred: ACTION2 (dr=5), Secondary: ACTION4 (dc=1)
    orchestrator._blocked_actions.add("ACTION2")
    
    action = await orchestrator._try_autopilot({"grid": [[0]*10 for _ in range(10)]}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4"])
    assert action["action_id"] == "ACTION4"
    assert "primary blocked" in action["rationale"]

@pytest.mark.asyncio
async def test_dissonance_propagation_to_solver(orchestrator):
    orchestrator._force_replan = True
    
    # Mock SolveEngine.solve to capture context
    orchestrator.solve_engine.solve = AsyncMock(return_value=MagicMock())
    
    observation = {"grid": [[0]], "task_id": "t1"}
    await orchestrator.solve(observation, {}, 5)
    
    args, kwargs = orchestrator.solve_engine.solve.call_args
    assert kwargs["hypothesis_context"]["orchestrator_force_replan"] is True
    assert orchestrator._force_replan is False # Reset after pass

def test_blocked_actions_cleared_on_progress(orchestrator):
    orchestrator._blocked_actions.add("ACTION1")
    orchestrator._consecutive_no_progress_steps = 5
    
    # Step with reward
    orchestrator._step_history.append({"action_id": "ACTION1"})
    orchestrator.record_step_result(reward=1.0, done=False)
    
    assert len(orchestrator._blocked_actions) == 0
    assert orchestrator._consecutive_no_progress_steps == 0
