
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
    orchestrator = ARCOrchestrator(brain, llm, "session-1", serializer, {})
    return orchestrator

@pytest.fixture
def solver(orchestrator):
    return orchestrator.solve_engine

@pytest.mark.asyncio
async def test_enforce_policy_ignores_explore_chunk(orchestrator, solver):
    # Set up an explore chunk
    chunk = PlanChunk(
        description="try ACTION3",
        estimated_actions=["ACTION3"],
        source="explore"
    )
    solver._active_chunk = chunk
    orchestrator._solve_context = {"active_chunk": {"source": "explore", "estimated_actions": ["ACTION3"]}}
    
    available_actions = ["ACTION1", "ACTION2", "ACTION3"]
    # LLM picks a DIFFERENT unexplored action
    orchestrator._hypothesis_context = {
        "action_coverage": {"untested_actions": ["ACTION1", "ACTION2", "ACTION3"]}
    }
    llm_action = {"action_id": "ACTION1", "rationale": "intuition"}
    
    final_action = orchestrator._enforce_action_policy(llm_action, available_actions)
    
    # Should NOT override to ACTION3 because it's just 'explore'
    # It stays ACTION1 (which is valid unexplored)
    assert final_action["action_id"] == "ACTION1"
    # But it should STILL consume the explore chunk action if it matched (it didn't here)
    assert solver._active_chunk.estimated_actions == ["ACTION3"]

@pytest.mark.asyncio
async def test_enforce_policy_consumes_explore_chunk_on_match(orchestrator, solver):
    chunk = PlanChunk(
        description="try ACTION1",
        estimated_actions=["ACTION1"],
        source="explore"
    )
    solver._active_chunk = chunk
    orchestrator._solve_context = {"active_chunk": {"source": "explore", "estimated_actions": ["ACTION1"]}}
    
    available_actions = ["ACTION1", "ACTION2"]
    orchestrator._hypothesis_context = {"action_coverage": {"untested_actions": ["ACTION1"]}}
    llm_action = {"action_id": "ACTION1", "rationale": "match"}
    
    final_action = orchestrator._enforce_action_policy(llm_action, available_actions)
    
    assert final_action["action_id"] == "ACTION1"
    # Should be consumed
    assert solver._active_chunk.estimated_actions == []

@pytest.mark.asyncio
async def test_enforce_policy_strict_bfs(orchestrator, solver):
    # BFS: ["ACTION1", "ACTION2"]
    # If ACTION1 is blocked, do NOT enforce ACTION2 even if it is available.
    chunk = PlanChunk(
        description="strict path",
        estimated_actions=["ACTION1", "ACTION2"],
        source="bfs"
    )
    solver._active_chunk = chunk
    orchestrator._solve_context = {"active_chunk": {"source": "bfs", "description": "strict path", "estimated_actions": ["ACTION1", "ACTION2"]}}
    
    # ACTION1 is NOT available
    available_actions = ["ACTION2", "ACTION3"]
    llm_action = {"action_id": "ACTION3", "rationale": "something else"}
    
    final_action = orchestrator._enforce_action_policy(llm_action, available_actions)
    
    # Should NOT override to ACTION2 because BFS first step failed
    assert final_action["action_id"] == "ACTION3"
    assert solver._active_chunk.estimated_actions == ["ACTION1", "ACTION2"]

@pytest.mark.asyncio
async def test_enforce_policy_loose_directional(orchestrator, solver):
    # Directional: ["ACTION1", "ACTION2"]
    # If ACTION1 is blocked, skip to ACTION2 if available.
    chunk = PlanChunk(
        description="move toward goal",
        estimated_actions=["ACTION1", "ACTION2"],
        source="directional"
    )
    solver._active_chunk = chunk
    orchestrator._solve_context = {"active_chunk": {"source": "directional", "description": "move toward goal", "estimated_actions": ["ACTION1", "ACTION2"]}}
    
    available_actions = ["ACTION2", "ACTION3"]
    llm_action = {"action_id": "ACTION3", "rationale": "something else"}
    
    final_action = orchestrator._enforce_action_policy(llm_action, available_actions)
    
    # Should override to ACTION2 (skipping ACTION1)
    assert final_action["action_id"] == "ACTION2"
    assert "enforcing directional chunk" in final_action["rationale"]
    # Should have popped both ACTION1 and ACTION2
    assert solver._active_chunk.estimated_actions == []

@pytest.mark.asyncio
async def test_enforce_policy_skips_stale_low_value_directional_actions(orchestrator, solver):
    chunk = PlanChunk(
        description="move toward goal",
        estimated_actions=["ACTION1", "ACTION2"],
        source="directional"
    )
    solver._active_chunk = chunk
    orchestrator._solve_context = {
        "active_chunk": {
            "source": "directional",
            "description": "move toward goal",
            "estimated_actions": ["ACTION1", "ACTION2"],
        }
    }
    orchestrator._hypothesis_context = {
        "observed_action_effects": [
            {
                "action": "ACTION1",
                "value_status": "low_value",
                "over_retest_budget": True,
                "zero_reward_streak": 3,
                "no_progress_count": 2,
                "rank_score": 0.05,
                "avg_meaningful_change": 0.12,
            },
            {
                "action": "ACTION2",
                "value_status": "tentative",
                "over_retest_budget": False,
                "zero_reward_streak": 1,
                "no_progress_count": 0,
                "rank_score": 0.34,
                "avg_meaningful_change": 0.40,
            },
        ]
    }

    available_actions = ["ACTION1", "ACTION2", "ACTION3"]
    llm_action = {"action_id": "ACTION3", "rationale": "something else"}

    final_action = orchestrator._enforce_action_policy(llm_action, available_actions)

    assert final_action["action_id"] == "ACTION2"
    assert "enforcing directional chunk" in final_action["rationale"]
    assert solver._active_chunk.estimated_actions == []

@pytest.mark.asyncio
async def test_solve_engine_stale_bfs_detection(solver):
    # Setup a BFS chunk
    solver._active_chunk = PlanChunk(
        description="path",
        estimated_actions=["ACTION1"],
        source="bfs"
    )
    
    # ACTION1 is blocked
    obs = {"available_actions": ["ACTION2"], "task_id": "t1", "dataset_id": "d1"}
    ctx = {"last_transition_effect": {"meaningful_change_score": 0.5}}
    
    # Running solve should discard the stale BFS chunk
    await solver.solve(obs, ctx, step=1, state_graph=MagicMock(), current_state_hash="h1")
    
    # It might have generated a new 'explore' chunk, but the 'bfs' one should be gone
    assert solver._active_chunk is None or solver._active_chunk.source != "bfs"

@pytest.mark.asyncio
async def test_solve_engine_loose_directional_stale_detection(solver):
    # Directional: ["ACTION1", "ACTION2"]
    # If ACTION1 is blocked but ACTION2 is available, it is NOT stale yet.
    solver._active_chunk = PlanChunk(
        description="toward goal",
        estimated_actions=["ACTION1", "ACTION2"],
        source="directional"
    )
    
    obs = {"available_actions": ["ACTION2"], "task_id": "t1", "dataset_id": "d1"}
    ctx = {"last_transition_effect": {"meaningful_change_score": 0.5}}
    
    await solver.solve(obs, ctx, step=1, state_graph=MagicMock(), current_state_hash="h1")
    
    assert solver._active_chunk is not None
    assert solver._active_chunk.source == "directional"
    assert solver._active_chunk.estimated_actions == ["ACTION1", "ACTION2"]
