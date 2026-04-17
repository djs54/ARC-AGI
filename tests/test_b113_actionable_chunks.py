
import pytest
from unittest.mock import AsyncMock, MagicMock
from agents.arc3.solver import SolveEngine, PlanChunk, RoleType, ObjectRole, VictoryCondition, VictoryType
from agents.arc3.orchestrator import ARCOrchestrator
from benchmarks.arc3.state_serializer import StateSerializerForARC

@pytest.fixture
def brain():
    brain = AsyncMock()
    brain.recall_plans.return_value = {"plans": []}
    brain.recall_relevant_lessons.return_value = {"lessons": []}
    brain.analogical_search.return_value = {"results": []}
    brain.register_plan.return_value = {"plan_id": "p-new"}
    return brain

@pytest.mark.asyncio
async def test_solve_engine_replenishes_directional_chunk_when_low(brain):
    llm = AsyncMock()
    engine = SolveEngine(brain, llm, "s1")
    engine._victory_condition = VictoryCondition(
        condition_type=VictoryType.REACH_GOAL, confidence=0.9, description="reach exit"
    )
    engine._archetype_confidence = 0.8
    engine._object_roles = {
        1: ObjectRole(color_id=1, role=RoleType.PLAYER, confidence=0.9, estimated_position={"row": 10.0, "col": 10.0}),
        9: ObjectRole(color_id=9, role=RoleType.GOAL, confidence=0.9, estimated_position={"row": 15.0, "col": 15.0}),
    }
    
    # Active directional chunk with only 1 action left
    engine._active_chunk = PlanChunk(
        description="move toward goal (dist=10)",
        estimated_actions=["ACTION1"], # Only 1 left
        source="directional",
        plan_id="p-old"
    )
    
    obs = {
        "available_actions": ["ACTION1", "ACTION2", "ACTION3", "ACTION4"],
        "task_id": "t1", "dataset_id": "d1", "grid": [[0]]
    }
    ctx = {
        "last_transition_effect": {"meaningful_change_score": 0.5},
        "action_facts": [
            {"action": "ACTION1", "trend": {"kind": "directional_drift", "axis": "row", "direction": "down"}},
            {"action": "ACTION2", "trend": {"kind": "directional_drift", "axis": "col", "direction": "right"}},
        ],
        "action_coverage": {"initial_exploration_complete": True, "tested_count": 4, "untested_count": 0}
    }
    
    # We don't mock the whole generate_chunk, we let it run to see if it produces fresh actions.
    # The graduation assessment should pass because we set up roles and facts above.
    
    # Step 6 should clear the old chunk because it's running low (len=1 < 2)
    # Then it should generate a new one.
    await engine.solve(obs, ctx, step=5, state_graph=MagicMock(), current_state_hash="h1")
    
    assert engine._active_chunk is not None
    assert engine._active_chunk.source == "directional"
    # Should have more than 1 action now
    assert len(engine._active_chunk.estimated_actions) > 1
    assert "dist=10" in engine._active_chunk.description

@pytest.mark.asyncio
async def test_solve_engine_clears_exhausted_bfs_chunk(brain):
    engine = SolveEngine(brain, MagicMock(), "s1")
    engine._victory_condition = VictoryCondition(condition_type=VictoryType.REACH_GOAL, confidence=0.9, description="test")
    
    # Exhausted BFS chunk
    engine._active_chunk = PlanChunk(
        description="path",
        estimated_actions=[], # Exhausted
        source="bfs",
        plan_id="p-old"
    )
    
    obs = {"available_actions": ["ACTION1"], "task_id": "t1", "dataset_id": "d1", "grid": [[0]]}
    ctx = {"last_transition_effect": {"meaningful_change_score": 0.5}}
    
    # Step 6 should clear it and generate a new one (likely 'explore' because no roles/graph)
    await engine.solve(obs, ctx, step=5, state_graph=MagicMock(), current_state_hash="h1")
    
    assert engine._active_chunk is not None
    assert engine._active_chunk.description != "path"
    assert engine._active_chunk.source in ("explore", "directional", "bfs")
    assert len(engine._active_chunk.estimated_actions) > 0
