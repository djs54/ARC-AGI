
import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.solver import SolveEngine, PlanChunk
from benchmarks.arc3.state_serializer import StateSerializerForARC

@pytest.fixture
def orchestrator():
    brain = AsyncMock()
    llm = MagicMock()
    serializer = StateSerializerForARC()
    orchestrator = ARCOrchestrator(brain, llm, "session-1", serializer, {})
    return orchestrator

@pytest.fixture
def solver(orchestrator):
    return orchestrator.solve_engine

def test_peek_action_consequences(solver):
    # Setup facts
    hyp_ctx = {
        "action_facts": [
            {"action": "ACTION1", "description": "shifts up"}
        ]
    }
    # Setup active chunk
    solver._active_chunk = PlanChunk(
        description="move toward goal",
        estimated_actions=["ACTION1", "ACTION2"],
        source="directional"
    )
    
    # Test action with fact and chunk match
    res1 = solver.peek_action_consequences("ACTION1", hyp_ctx)
    assert res1["has_fact"] is True
    assert res1["fact_summary"] == "shifts up"
    assert res1["matches_active_chunk"] is True
    
    # Test action with no fact and no chunk match
    res2 = solver.peek_action_consequences("ACTION3", hyp_ctx)
    assert res2["has_fact"] is False
    assert res2["matches_active_chunk"] is False

@pytest.mark.asyncio
async def test_mental_sandbox_tool_use(orchestrator, solver):
    # Mock LLM to use the tool then decide
    responses = [
        json.dumps({"thought": "I want to check ACTION1", "sandbox_thought": "ACTION1"}),
        json.dumps({"action_id": "ACTION1", "rationale": "it matches my plan"})
    ]
    orchestrator.llm.chat = MagicMock(side_effect=responses)
    
    # Setup some context for the peek
    orchestrator._hypothesis_context = {"action_facts": []}
    solver._active_chunk = PlanChunk(description="test", estimated_actions=["ACTION1"])
    
    action = await orchestrator._mental_sandbox("initial prompt", ["ACTION1"], {})
    
    assert action["action_id"] == "ACTION1"
    assert "(sandbox refined)" in action["rationale"]
    assert len(action["thinking_trace"]) == 1
    assert action["thinking_trace"][0]["test_action"] == "ACTION1"
    assert action["thinking_trace"][0]["result"]["matches_active_chunk"] is True

@pytest.mark.asyncio
async def test_mental_sandbox_direct_decision(orchestrator):
    # Mock LLM to decide immediately
    orchestrator.llm.chat = MagicMock(return_value=json.dumps({"action_id": "ACTION2", "rationale": "direct"}))
    
    action = await orchestrator._mental_sandbox("initial prompt", ["ACTION2"], {})
    
    assert action["action_id"] == "ACTION2"
    assert action["rationale"] == "direct"
    assert len(action.get("thinking_trace", [])) == 0

@pytest.mark.asyncio
async def test_act_records_thinking_trace(orchestrator, solver):
    # Integrate into full act call
    # Make it return a final action on second call to avoid infinite loop or fallback
    orchestrator.llm.chat.side_effect = [
        json.dumps({"thought": "check ACTION1", "sandbox_thought": "ACTION1"}),
        json.dumps({"action_id": "ACTION1", "rationale": "good"})
    ]
    
    obs = {
        "dataset_id": "arc", "task_id": "t1", "episode_num": 1, "step_num": 1,
        "grid": [[0]], "colors": [], "shapes": [], "available_actions": ["ACTION1"]
    }
    memory_ctx = {"_triggered": False}
    
    with patch.object(orchestrator, "_summarize_puzzle_structure", return_value="summary"):
        await orchestrator.act(obs, memory_ctx, step_num=1)
    
    last_step = orchestrator._step_history[-1]
    assert "thinking_trace" in last_step
    assert len(last_step["thinking_trace"]) == 1
    assert last_step["thinking_trace"][0]["test_action"] == "ACTION1"
