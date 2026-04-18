
import pytest
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.orchestrator import ARCOrchestrator

@pytest.mark.asyncio
async def test_plan_registration_idempotency():
    brain = AsyncMock()
    brain.recall_plans.return_value = {"plans": []}
    brain.notify_turn.return_value = {"status": "ok"}
    brain.register_plan.return_value = {"plan_id": "plan-123"}
    
    config = {"llm": {"model": "test"}}
    orchestrator = ARCOrchestrator(brain, MagicMock(), "session-1", MagicMock(), config)
    
    observation = {"dataset_id": "d", "task_id": "t"}
    memory_context = {}
    
    # First plan call
    orchestrator._solve_context = {
        "archetype": "space",
        "archetype_confidence": 0.8,
        "victory_condition": {"type": "reach_goal", "confidence": 0.8},
        "active_chunk": {"description": "test plan"}
    }
    # Mock _draft_plan_steps to return consistent steps
    orchestrator._draft_plan_steps = MagicMock(return_value=["step 1"])
    
    res1 = await orchestrator.plan(observation, memory_context)
    assert res1["plan_id"] == "plan-123"
    assert brain.register_plan.call_count == 1
    
    # Second plan call with identical content
    res2 = await orchestrator.plan(observation, memory_context)
    assert res2["plan_id"] == "plan-123"
    # Should NOT have called register_plan again
    assert brain.register_plan.call_count == 1
    
    # Third plan call with DIFFERENT content (new steps)
    orchestrator._draft_plan_steps = MagicMock(return_value=["step 1", "step 2"])
    brain.register_plan.return_value = {"plan_id": "plan-456"}
    res3 = await orchestrator.plan(observation, memory_context)
    assert res3["plan_id"] == "plan-456"
    assert brain.register_plan.call_count == 2
