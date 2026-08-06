import pytest
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.orchestrator import ARCOrchestrator
from benchmarks.arc3.state_serializer import StateSerializerForARC

@pytest.mark.asyncio
async def test_perceive_step_response_reconciles_delta():
    """A041: Verify perceive_step_response uses pre-computed FrameDelta from history."""
    brain = MagicMock()
    brain.notify_turn = AsyncMock(return_value={"status": "ok"})
    brain.upsert_lesson = AsyncMock(return_value={"lesson_id": "L1"})
    
    orchestrator = ARCOrchestrator(
        brain_client=brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    
    # 1. Setup history with a FrameDelta showing 42 cells changed
    orchestrator._step_history = [
        {
            "step": 1,
            "action_id": "ACTION1",
            "frame_delta": {
                "n_cells_changed": 42,
                "apparent_effect": "large_transformation",
                "direction": None
            }
        }
    ]
    
    observation = {
        "grid": [[0, 0], [0, 0]],
        "state": "NOT_FINISHED"
    }
    
    # 2. Call perceive_step_response
    # Even if _last_grid is None or same as current grid, it should pick up 42
    orchestrator._last_grid = [[0, 0], [0, 0]]
    
    perception = await orchestrator.perceive_step_response(
        observation, step=1, reward=0.0, done=False, action_id="ACTION1"
    )
    
    # 3. Assertions
    assert perception["delta"]["n_cells_changed"] == 42
    assert perception["delta"]["apparent_effect"] == "large_transformation"
    
    # Verify notify_turn content includes Delta=42
    call_args = brain.notify_turn.call_args_list[0]
    content = call_args.kwargs["content"]
    assert "Delta=42" in content
    assert "Expectation=unexpected movement" in content  # n_changed > 0, reward = 0

@pytest.mark.asyncio
async def test_perceive_step_response_falls_back_to_manual_count():
    """A041: Verify fallback to manual count when no FrameDelta is found."""
    brain = MagicMock()
    brain.notify_turn = AsyncMock(return_value={"status": "ok"})
    brain.upsert_lesson = AsyncMock(return_value={"lesson_id": "L1"})
    
    orchestrator = ARCOrchestrator(
        brain_client=brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    
    # No history or FrameDelta
    orchestrator._step_history = []
    orchestrator._last_grid = [[0, 0], [0, 0]]
    observation = {
        "grid": [[1, 1], [1, 1]], # 4 cells changed
        "state": "NOT_FINISHED"
    }
    
    perception = await orchestrator.perceive_step_response(
        observation, step=1, reward=0.0, done=False, action_id="ACTION1"
    )
    
    assert perception["delta"]["n_cells_changed"] == 4
