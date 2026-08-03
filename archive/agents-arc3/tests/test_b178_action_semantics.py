import pytest
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.solver import RoleType, ObjectRole
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

def test_pick_action_for_direction_default(orchestrator):
    # No empirical map, should use convention
    # Up: dr < 0
    assert orchestrator._pick_action_for_direction(-5, 0, ["ACTION1", "ACTION2"]) == "ACTION1"
    # Down: dr > 0
    assert orchestrator._pick_action_for_direction(5, 0, ["ACTION1", "ACTION2"]) == "ACTION2"
    # Left: dc < 0
    assert orchestrator._pick_action_for_direction(0, -5, ["ACTION3", "ACTION4"]) == "ACTION3"
    # Right: dc > 0
    assert orchestrator._pick_action_for_direction(0, 5, ["ACTION3", "ACTION4"]) == "ACTION4"

def test_pick_action_for_direction_empirical(orchestrator):
    # Setup empirical map where directions are swapped
    # ACTION1 moves LEFT (0, -1), ACTION2 moves RIGHT (0, 1)
    # ACTION3 moves UP (-1, 0), ACTION4 moves DOWN (1, 0)
    orchestrator._action_direction_map = {
        "ACTION1": (0.0, -1.0),
        "ACTION2": (0.0, 1.0),
        "ACTION3": (-1.0, 0.0),
        "ACTION4": (1.0, 0.0),
    }
    
    available = ["ACTION1", "ACTION2", "ACTION3", "ACTION4"]
    
    # Desired: UP (dr < 0) -> should pick ACTION3
    assert orchestrator._pick_action_for_direction(-5, 0, available) == "ACTION3"
    # Desired: RIGHT (dc > 0) -> should pick ACTION2
    assert orchestrator._pick_action_for_direction(0, 5, available) == "ACTION2"

def test_pick_action_for_direction_diagonal(orchestrator):
    # ACTION1: (-1, -1) (Up-Left)
    # ACTION2: (1, 1) (Down-Right)
    orchestrator._action_direction_map = {
        "ACTION1": (-1.0, -1.0),
        "ACTION2": (1.0, 1.0),
    }
    
    available = ["ACTION1", "ACTION2"]
    
    # Desired: Up-Left (-5, -5) -> dot product with (-1, -1) is 10, with (1, 1) is -10.
    assert orchestrator._pick_action_for_direction(-5, -5, available) == "ACTION1"
    # Desired: Down-Right (5, 5) -> should pick ACTION2
    assert orchestrator._pick_action_for_direction(5, 5, available) == "ACTION2"

@pytest.mark.asyncio
async def test_autopilot_uses_discovered_map(orchestrator):
    orchestrator.solve_engine._object_roles = {
        1: ObjectRole(color_id=1, role=RoleType.PLAYER, confidence=0.9, estimated_position={"row": 5.0, "col": 5.0}),
        2: ObjectRole(color_id=2, role=RoleType.GOAL, confidence=0.9, estimated_position={"row": 0.0, "col": 5.0})
    }
    # Desired: UP (dr = -5)
    
    # Swapped map: ACTION2 is UP
    orchestrator._action_direction_map = {
        "ACTION2": (-1.0, 0.0),
        "ACTION1": (1.0, 0.0),
    }
    
    action = await orchestrator._try_autopilot({"grid": [[0]*10 for _ in range(10)]}, ["ACTION1", "ACTION2"])
    assert action["action_id"] == "ACTION2"
    assert "discovered mapping" in action["rationale"]

@pytest.mark.asyncio
async def test_action_map_loading_in_act(orchestrator):
    mock_eg = MagicMock()
    mock_eg.get_action_directions = AsyncMock(return_value={"ACTION1": (0.0, -1.0)})
    orchestrator._entity_graph = mock_eg
    orchestrator._task_id = "t1"
    
    # act() should trigger load
    observation = {"grid": [[0]], "state": "RUNNING", "available_actions": ["ACTION1"], "colors": [], "shapes": []}
    
    # Mock solve to avoid crash
    orchestrator.solve_engine.solve = AsyncMock(return_value=MagicMock())
    
    await orchestrator.act(observation, {}, 1)
    
    assert orchestrator._action_direction_map == {"ACTION1": (0.0, -1.0)}
    assert mock_eg.get_action_directions.called

def test_map_reset_on_level_transition(orchestrator):
    orchestrator._action_direction_map = {"A1": (1, 0)}
    orchestrator.brain.report_outcome = AsyncMock() # Required for _on_level_transition
    
    # Mock _save_puzzle_model to avoid DB calls
    orchestrator._save_puzzle_model = AsyncMock()
    
    import asyncio
    asyncio.run(orchestrator._on_level_transition(1, []))
    
    assert orchestrator._action_direction_map is None
