import pytest
import datetime
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.solver import SolveEngine, VictoryCondition, VictoryType, ObjectRole, RoleType

@pytest.mark.asyncio
async def test_victory_condition_persistence():
    db = MagicMock()
    db.execute_write = AsyncMock()
    
    engine = SolveEngine(MagicMock(), MagicMock(), "session-1")
    # Mock entity graph
    mock_eg = MagicMock()
    mock_eg.db = db
    engine._entity_graph = mock_eg
    engine._task_id = "task-1"
    engine._current_level = 0
    
    vc = VictoryCondition(
        condition_type=VictoryType.REACH_GOAL,
        description="reach red square",
        target_color_id=2,
        confidence=0.9,
        evidence_steps=[1, 2, 3],
        source="llm"
    )
    
    # 1. Set VC
    engine._set_victory_condition(vc)
    assert engine._pending_vc_write == vc
    
    # 2. Flush VC
    await engine._flush_victory_condition()
    
    # Verify MERGE query
    assert db.execute_write.called
    write_calls = [c[0][0] for c in db.execute_write.call_args_list]
    
    # Check main node merge
    assert any("MERGE (v:VictoryCondition {condition_id: $cid})" in q for q in write_calls)
    # Check relationship merge (target_color_id was 2)
    assert any("MERGE (v)-[:REQUIRES_ENTITY {requirement: $req}]->(e)" in q for q in write_calls)
    
    # Verify params
    params = db.execute_write.call_args_list[0][0][1]
    assert params["tid"] == "task-1"
    assert params["ctype"] == "reach_goal"
    assert params["tcid"] == 2
    assert params["steps"] == "1,2,3"

@pytest.mark.asyncio
async def test_victory_condition_load():
    db = MagicMock()
    # Mock return row
    db.execute_read = AsyncMock(return_value=[
        {
            "v.condition_type": "reach_goal",
            "v.description": "desc from db",
            "v.target_color_id": 5,
            "v.confidence": 0.85,
            "v.source": "recall_plans",
            "v.evidence_steps": "10,11"
        }
    ])
    
    engine = SolveEngine(MagicMock(), MagicMock(), "session-1")
    mock_eg = MagicMock()
    mock_eg.db = db
    engine._entity_graph = mock_eg
    engine._task_id = "task-1"
    engine._current_level = 1
    
    await engine._load_victory_condition()
    
    assert engine._victory_condition is not None
    assert engine._victory_condition.condition_type == VictoryType.REACH_GOAL
    assert engine._victory_condition.description == "desc from db"
    assert engine._victory_condition.target_color_id == 5
    assert engine._victory_condition.evidence_steps == [10, 11]
    assert engine._victory_condition.source == "recall_plans"

@pytest.mark.asyncio
async def test_solve_triggers_sync_and_flush():
    brain = MagicMock()
    brain.register_plan = AsyncMock(return_value={"plan_id": "p1"})
    brain.recall_plans = AsyncMock(return_value={"plans": []})
    brain.recall_relevant_lessons = AsyncMock(return_value={"lessons": []})
    brain.analogical_search = AsyncMock(return_value={"results": []})
    
    engine = SolveEngine(brain, MagicMock(), "session-1")
    mock_graph = MagicMock()
    mock_graph.db = MagicMock()
    mock_graph.db.execute_write = AsyncMock()
    mock_graph.db.execute_read = AsyncMock(return_value=[])
    mock_graph.load_all_roles = AsyncMock(return_value={})
    
    engine._entity_graph = mock_graph
    engine._task_id = "t1"
    engine._current_level = 0
    
    # Mock victory hypothesizer to return a new VC
    engine.victory_hypothesizer.hypothesize = AsyncMock(return_value=VictoryCondition(
        condition_type=VictoryType.COLLECT_ALL, confidence=0.9
    ))
    # Threshold for calling hypothesizer is confidence >= 0.65 (CALL_THRESHOLD)
    engine._archetype_confidence = 0.7 
    
    obs = {"grid": [[0]], "task_id": "t1", "dataset_id": "d1", "available_actions": ["A1"]}
    ctx = {"last_transition_effect": {"reward_signal": 0.0}}
    
    await engine.solve(obs, ctx, step=0, state_graph=MagicMock(), current_state_hash="h1")
    
    # Should have called load (at start) and flush (at end)
    # execute_read was for _load_victory_condition
    assert mock_graph.db.execute_read.called
    # execute_write was for _flush_victory_condition
    assert mock_graph.db.execute_write.called
