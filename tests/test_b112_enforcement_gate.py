
import pytest
from unittest.mock import AsyncMock, MagicMock
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.solver import SolveEngine, PlanChunk, SolveContext
from benchmarks.arc3.state_serializer import StateSerializerForARC

@pytest.fixture
def orchestrator():
    brain = AsyncMock()
    llm = MagicMock()
    serializer = StateSerializerForARC()
    return ARCOrchestrator(brain, llm, "session-1", serializer, {})

@pytest.fixture
def solver(orchestrator):
    return orchestrator.solve_engine

@pytest.mark.asyncio
async def test_enforce_policy_ignores_explore_chunk(orchestrator, solver):
    # Set up an 'explore' chunk
    solver._active_chunk = PlanChunk(
        description="test explore",
        estimated_actions=["ACTION1"],
        source="explore"
    )
    orchestrator._solve_context = {
        "active_chunk": {
            "description": "test explore",
            "estimated_actions": ["ACTION1"],
            "source": "explore"
        }
    }
    
    # LLM chooses ACTION2
    action = {"action_id": "ACTION2", "rationale": "llm choice"}
    available = ["ACTION1", "ACTION2"]
    
    # POLICY: should NOT override since it's an 'explore' chunk
    final_action = orchestrator._enforce_action_policy(action, available)
    
    assert final_action["action_id"] == "ACTION2"
    assert "policy override" not in final_action["rationale"]

@pytest.mark.asyncio
async def test_enforce_policy_enforces_bfs_chunk(orchestrator, solver):
    # Set up a 'bfs' chunk
    solver._active_chunk = PlanChunk(
        description="test bfs",
        estimated_actions=["ACTION1"],
        source="bfs"
    )
    orchestrator._solve_context = {
        "active_chunk": {
            "description": "test bfs",
            "estimated_actions": ["ACTION1"],
            "source": "bfs"
        }
    }
    
    # LLM chooses ACTION2
    action = {"action_id": "ACTION2", "rationale": "llm choice"}
    available = ["ACTION1", "ACTION2"]
    
    # POLICY: SHOULD override since it's a guidance-grade chunk
    final_action = orchestrator._enforce_action_policy(action, available)
    
    assert final_action["action_id"] == "ACTION1"
    assert "enforcing bfs chunk" in final_action["rationale"]
    # Check that it was popped from solver
    assert solver._active_chunk.estimated_actions == []

@pytest.mark.asyncio
async def test_solve_engine_bfs_strict_stale_detection(solver):
    # Active BFS chunk: next step is ACTION1
    solver._active_chunk = PlanChunk(
        description="path",
        estimated_actions=["ACTION1", "ACTION2"],
        source="bfs"
    )
    
    # ACTION1 is blocked
    obs = {"available_actions": ["ACTION2", "ACTION3"]}
    ctx = {"last_transition_effect": {"meaningful_change_score": 0.5}}
    
    # solve() should discard BFS chunk because first action is blocked
    await solver.solve(obs, ctx, step=1, state_graph=MagicMock(), current_state_hash="h1")
    
    assert solver._active_chunk is None or solver._active_chunk.source != "bfs"

@pytest.mark.asyncio
async def test_solve_engine_directional_loose_stale_detection(solver):
    # Active directional chunk: next step is ACTION1
    solver._active_chunk = PlanChunk(
        description="toward goal",
        estimated_actions=["ACTION1", "ACTION2"],
        source="directional"
    )
    
    # ACTION1 is blocked, but ACTION2 is valid
    obs = {"available_actions": ["ACTION2", "ACTION3"]}
    ctx = {"last_transition_effect": {"meaningful_change_score": 0.5}}
    
    # solve() should NOT discard directional chunk since one action remains valid
    await solver.solve(obs, ctx, step=1, state_graph=MagicMock(), current_state_hash="h1")
    
    assert solver._active_chunk is not None
    assert solver._active_chunk.source == "directional"
    assert solver._active_chunk.estimated_actions == ["ACTION1", "ACTION2"]
