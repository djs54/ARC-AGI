import pytest
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.solver import RoleType, ObjectRole, SolveEngine
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
    # Mock pattern tracker
    orch._pattern_tracker.update = MagicMock(return_value={
        "phase": "finish",
        "similarity": 1.0,
        "similarity_trend": "stable"
    })
    return orch

@pytest.mark.asyncio
async def test_wall_detection_and_rotation(orchestrator):
    # Setup: player at 5,5, target at 10,10. Row delta 5, col delta 5.
    # Autopilot prefers row axis (ACTION2).
    orchestrator.solve_engine._object_roles = {
        1: ObjectRole(color_id=1, role=RoleType.PLAYER, confidence=0.9, estimated_position={"row": 5.0, "col": 5.0}),
        2: ObjectRole(color_id=2, role=RoleType.GOAL, confidence=0.9, estimated_position={"row": 10.0, "col": 10.0})
    }
    
    # 1. First autopilot step: moves player to 5,5 (tried ACTION2 but stayed at 5,5)
    # Simulate first step
    action = await orchestrator._try_autopilot({"grid": [[0]*20 for _ in range(20)]}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4"])
    assert action["action_id"] == "ACTION2"
    
    # Record result: no movement
    orchestrator._step_history.append(action)
    orchestrator.record_step_result(reward=0.0, done=False, next_observation={"grid": [[0]*20 for _ in range(20)]})
    
    # 2. Second autopilot step: should detect row wall and rotate to col axis (ACTION4)
    action2 = await orchestrator._try_autopilot({"grid": [[0]*20 for _ in range(20)]}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4"])
    assert action2["action_id"] == "ACTION4"
    assert "row blocked" in action2["rationale"]
    assert "row" in orchestrator._blocked_axes

@pytest.mark.asyncio
async def test_blocked_axis_persistence(orchestrator):
    orchestrator.solve_engine._object_roles = {
        1: ObjectRole(color_id=1, role=RoleType.PLAYER, confidence=0.9, estimated_position={"row": 5.0, "col": 5.0}),
        2: ObjectRole(color_id=2, role=RoleType.GOAL, confidence=0.9, estimated_position={"row": 10.0, "col": 10.0})
    }
    
    # Mark row blocked
    orchestrator._blocked_axes["row"] = 10
    
    # Interleave an LLM step (not in history but step count increased)
    orchestrator._step_history.append({"decision_source": "llm", "step": 11})
    
    # Autopilot at step 12 should still see row blocked
    action = await orchestrator._try_autopilot({"grid": [[0]*20 for _ in range(20)]}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4"])
    assert action["action_id"] == "ACTION4" # Rotated to col axis
    assert "row blocked" in action["rationale"]

def test_blocked_axis_clearing_on_reward(orchestrator):
    orchestrator._blocked_axes["row"] = 10
    
    orchestrator._step_history.append({"decision_source": "autopilot", "action_id": "ACTION2", "step": 11})
    orchestrator.record_step_result(reward=1.0, done=False, next_observation={"grid": [[0]*20 for _ in range(20)]})
    
    assert len(orchestrator._blocked_axes) == 0

@pytest.mark.asyncio
async def test_disengage_when_both_blocked(orchestrator):
    orchestrator.solve_engine._object_roles = {
        1: ObjectRole(color_id=1, role=RoleType.PLAYER, confidence=0.9, estimated_position={"row": 5.0, "col": 5.0}),
        2: ObjectRole(color_id=2, role=RoleType.GOAL, confidence=0.9, estimated_position={"row": 10.0, "col": 10.0})
    }
    
    step = 20
    orchestrator._blocked_axes["row"] = step - 1
    orchestrator._blocked_axes["col"] = step - 1
    
    action = await orchestrator._try_autopilot({"grid": [[0]*20 for _ in range(20)]}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4"])
    assert action is None # Disengaged because both axes blocked

@pytest.mark.asyncio
async def test_oscillation_breakout_still_works(orchestrator):
    # Regression test for B168 oscillation logic
    orchestrator.solve_engine._object_roles = {
        1: ObjectRole(color_id=1, role=RoleType.PLAYER, confidence=0.9, estimated_position={"row": 5.0, "col": 5.0}),
        2: ObjectRole(color_id=2, role=RoleType.GOAL, confidence=0.9, estimated_position={"row": 10.0, "col": 10.0})
    }
    
    # Add history of bouncing between 5,5 and 6,5
    for i in range(4):
        pos = (5.0, 5.0) if i % 2 == 0 else (6.0, 5.0)
        orchestrator._step_history.append({
            "decision_source": "autopilot",
            "autopilot_player_row": pos[0],
            "autopilot_player_col": pos[1]
        })
    
    # Should detect oscillation and switch axis
    action = await orchestrator._try_autopilot({"grid": [[0]*20 for _ in range(20)]}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4"])
    assert "oscillation detected" in action["rationale"]
    assert "switching axis" in action["rationale"]
