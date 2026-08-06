import pytest
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.entity_graph import EntityGraphBuilder
from agents.arc3.solver import SolveEngine, ObjectRole, RoleType

@pytest.mark.asyncio
async def test_entity_graph_role_persistence():
    db = MagicMock()
    db.execute_write = AsyncMock()
    db.execute_read = AsyncMock(return_value=[
        {"color_id": 1, "role": "player", "confidence": 0.9, "crow": 5.0, "ccol": 5.0}
    ])
    
    builder = EntityGraphBuilder(db, "task-1")
    
    # 1. Persist
    await builder.persist_role(1, "player", 0.9, {"row": 5.0, "col": 5.0}, level=0)
    
    # Verify write query
    assert db.execute_write.called
    query = db.execute_write.call_args[0][0]
    assert "MERGE (e:GridEntity {entity_id: $eid})" in query
    
    # 2. Load
    roles = await builder.load_all_roles(level=0)
    assert 1 in roles
    assert roles[1].role == RoleType.PLAYER
    assert roles[1].confidence == 0.9
    assert roles[1].estimated_position == {"row": 5.0, "col": 5.0}

@pytest.mark.asyncio
async def test_solve_engine_kuzu_sync():
    brain = MagicMock()
    # Mocking brain tools used in solve()
    brain.register_plan = AsyncMock(return_value={"plan_id": "p1"})
    brain.recall_plans = AsyncMock(return_value={"plans": []})
    brain.recall_relevant_lessons = AsyncMock(return_value={"lessons": []})
    
    engine = SolveEngine(brain, MagicMock(), "session-1")
    
    mock_graph = MagicMock()
    # Mock DB return: color 2 is goal
    mock_graph.load_all_roles = AsyncMock(return_value={
        2: ObjectRole(color_id=2, role=RoleType.GOAL, confidence=0.8)
    })
    mock_graph.persist_role = AsyncMock()
    
    engine._entity_graph = mock_graph
    
    # Grid where color 1 is player (heuristic should find it)
    grid = [[0]*10 for _ in range(10)]
    grid[0][0] = 1; grid[0][1] = 1 # 2-cell object
    
    observation = {
        "grid": grid,
        "colors": [{"value": 1, "count": 2}, {"value": 2, "count": 1}],
        "task_id": "t1"
    }
    # Mock role mapper to find player
    engine.role_mapper.update = MagicMock(return_value={
        1: ObjectRole(color_id=1, role=RoleType.PLAYER, confidence=0.7)
    })
    
    # Run solve
    await engine.solve(
        observation=observation,
        hypothesis_context={"current_state_hash": "h1"},
        step=0,
        state_graph=MagicMock(),
        current_state_hash="h1"
    )
    
    # Verify sync occurred
    assert mock_graph.load_all_roles.called
    # Verify both roles exist in cache
    assert 1 in engine._object_roles
    assert 2 in engine._object_roles
    assert engine._object_roles[2].role == RoleType.GOAL
    
    # Verify flush occurred (player was newly discovered)
    assert mock_graph.persist_role.called
    # Check if persist_role was called for color 1
    calls = [c[1]["color_id"] for c in mock_graph.persist_role.call_args_list if "color_id" in c[1]]
    if not calls:
        # Check positional args if kwargs not used
        calls = [c[0][0] for c in mock_graph.persist_role.call_args_list if c[0]]
    assert 1 in calls
