
import pytest
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.solver import ObjectRole, RoleType

@pytest.mark.asyncio
async def test_prompt_skip_noop_short_circuit():
    brain = AsyncMock()
    brain.notify_turn.return_value = {"status": "ok"}
    
    # Mock LLM to return an action
    serializer = MagicMock()
    serializer._estimate_tokens.return_value = 100
    orchestrator = ARCOrchestrator(brain, MagicMock(), "session-1", serializer, {})
    orchestrator.MAX_PROMPT_HISTORY = 10
    orchestrator._mental_sandbox = AsyncMock(return_value={"action_id": "ACTION6", "rationale": "steady state", "x": 0, "y": 0})
    
    observation = {
        "grid": [[0]],
        "colors": [{"value": 0, "count": 1}],
        "shapes": [],
        "frame_hash": "hash1",
        "available_actions": ["ACTION6"],
        "state": "NOT_FINISHED"
    }
    
    # Step 1: Normal call
    action1 = await orchestrator.act(observation, {}, step_num=1)
    assert action1["action_id"] == "ACTION6"
    assert orchestrator._mental_sandbox.call_count == 1
    
    # Step 2: Identical observation -> Should skip LLM
    action2 = await orchestrator.act(observation, {}, step_num=2)
    assert action2["action_id"] == "ACTION6"
    # Call count should still be 1
    assert orchestrator._mental_sandbox.call_count == 1
    assert any(event.get("operation") == "prompt_skip_noop" for event in orchestrator._execution_trace)
    
    # Step 3: Change in available actions -> Should trigger LLM
    obs2 = dict(observation)
    obs2["available_actions"] = ["ACTION6", "ACTION1"]
    action3 = await orchestrator.act(obs2, {}, step_num=3)
    assert orchestrator._mental_sandbox.call_count == 2
    
    # Step 4: Change in action facts -> Should trigger LLM
    orchestrator._hypothesis_context = {"action_facts": [{"action": "ACTION6", "description": "fact1"}]}
    action4 = await orchestrator.act(obs2, {}, step_num=4)
    assert orchestrator._mental_sandbox.call_count == 3
