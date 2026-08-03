
import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.solver import SolveEngine, PlanChunk, DecisionGuard
from benchmarks.arc3.state_serializer import StateSerializerForARC

@pytest.fixture
def guard():
    return DecisionGuard()

def test_guard_blocks_unavailable_action(guard):
    res = guard.critique_action("ACTION7", ["ACTION1", "ACTION2"], {}, None, [])
    assert res["status"] == "blocked"
    assert "not available" in res["reason"]
    assert res["suggested_action"] == "ACTION1"

def test_guard_warns_on_repeated_zero_pixel_change(guard):
    """Guard should warn when an action produced zero pixel change repeatedly."""
    history = [
        {"action_id": "ACTION1", "frame_delta": {"n_cells_changed": 0}},
        {"action_id": "ACTION1", "frame_delta": {"n_cells_changed": 0}},
    ]
    res = guard.critique_action("ACTION1", ["ACTION1", "ACTION2"], {}, None, history)
    assert res["status"] == "warned"
    assert "zero pixel change" in res["reason"]


def test_guard_approves_action_with_pixel_changes(guard):
    """Guard should NOT warn when an action produces pixel changes, even with reward=0."""
    history = [
        {"action_id": "ACTION1", "reward": 0.0, "frame_delta": {"n_cells_changed": 32}},
        {"action_id": "ACTION1", "reward": 0.0, "frame_delta": {"n_cells_changed": 33}},
    ]
    res = guard.critique_action("ACTION1", ["ACTION1", "ACTION2"], {}, None, history)
    assert res["status"] == "approved"

def test_guard_warns_on_chunk_deviation(guard):
    chunk = PlanChunk(description="test", estimated_actions=["ACTION2"], source="bfs")
    res = guard.critique_action("ACTION1", ["ACTION1", "ACTION2"], {}, chunk, [])
    assert res["status"] == "warned"
    assert "deviates from guidance-grade" in res["reason"]
    assert res["suggested_action"] == "ACTION2"

def test_guard_blocks_harmful_action(guard):
    hyp_ctx = {
        "action_facts": [
            {"action": "ACTION1", "value_status": "harmful", "description": "it kills you"}
        ]
    }
    res = guard.critique_action("ACTION1", ["ACTION1", "ACTION2"], hyp_ctx, None, [])
    assert res["status"] == "blocked"
    assert "marked as harmful" in res["reason"]

@pytest.mark.asyncio
async def test_act_applies_guard_override():
    brain = AsyncMock()
    llm = MagicMock()
    llm.chat.return_value = json.dumps({"action_id": "ACTION1", "rationale": "I like it"})
    
    orchestrator = ARCOrchestrator(brain, llm, "session-1", StateSerializerForARC(), {})
    orchestrator.solve_engine.decision_guard.critique_action = MagicMock(return_value={
        "status": "warned",
        "reason": "bad feeling",
        "suggested_action": "ACTION2"
    })
    
    obs = {
        "dataset_id": "arc", "task_id": "t1", "episode_num": 1, "step_num": 1,
        "grid": [[0]], "colors": [], "shapes": [], "available_actions": ["ACTION1", "ACTION2"]
    }
    
    with patch.object(orchestrator, "_summarize_puzzle_structure", return_value="summary"):
        action = await orchestrator.act(obs, {"_triggered": False}, step_num=1)
    
    assert action["action_id"] == "ACTION2"
    assert "guard override" in action["rationale"]
    assert action["guard_status"] == "warned"
