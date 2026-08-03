import pytest
import json
import hashlib
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.solver import SolveEngine, GameRuleHypothesis
from agents.arc3.orchestrator import ARCOrchestrator

@pytest.mark.asyncio
async def test_game_rule_hypothesis_persistence():
    db = MagicMock()
    db.execute_write = AsyncMock()
    db.execute_read = AsyncMock(return_value=[])
    
    engine = SolveEngine(MagicMock(), MagicMock(), "session-1")
    mock_eg = MagicMock()
    mock_eg.db = db
    engine._entity_graph = mock_eg
    engine._task_id = "task-1"
    
    grh = GameRuleHypothesis(
        rule_description="rule1",
        action_semantics={"A1": "up"},
        objective_description="reach goal",
        level_strategy="move up",
        confidence=0.9,
        evidence=["e1"],
        contradictions=[],
        source="llm"
    )
    
    # 1. Set GRH
    engine._set_game_rule_hypotheses([grh])
    assert engine._pending_grh_writes == [grh]
    
    # 2. Flush
    await engine._flush_grh_writes()
    
    # Verify MERGE query
    assert db.execute_write.called
    write_calls = [c[0][0] for c in db.execute_write.call_args_list]
    
    # Check main node merge
    assert any("MERGE (h:Hypothesis {id: $id})" in q for q in write_calls)
    # Check relationship merge (GENERALIZES)
    assert any("MERGE (h1)-[:GENERALIZES]->(h2)" in q for q in write_calls)
    
    # Verify params
    params = db.execute_write.call_args_list[0][0][1]
    assert params["descr"] == "rule1"
    assert params["cat"] == "game_rule"
    assert params["conf"] == 0.9
    assert "action_semantics" in params["raw"]

@pytest.mark.asyncio
async def test_game_rule_hypothesis_sync():
    db = MagicMock()
    # Mock return row
    raw_payload = json.dumps({
        "action_semantics": {"A1": "down"},
        "objective": "obj from db",
        "level_strategy": "strat from db",
        "evidence": ["e2"],
        "contradictions": []
    })
    db.execute_read = AsyncMock(return_value=[
        {
            "h.description": "rule from db",
            "h.confidence": 0.85,
            "h.game_type": "memory",
            "h.text_raw": raw_payload
        }
    ])
    
    engine = SolveEngine(MagicMock(), MagicMock(), "session-1")
    mock_eg = MagicMock()
    mock_eg.db = db
    engine._entity_graph = mock_eg
    engine._task_id = "task-1"
    
    await engine._sync_grh_from_db()
    
    assert len(engine._game_rule_hypotheses) == 1
    h = engine._game_rule_hypotheses[0]
    assert h.rule_description == "rule from db"
    assert h.confidence == 0.85
    assert h.action_semantics == {"A1": "down"}
    assert h.objective_description == "obj from db"

@pytest.mark.asyncio
async def test_orchestrator_reads_from_solver():
    orch = ARCOrchestrator(MagicMock(), MagicMock(), "session-1", MagicMock(), {})
    
    # Mock solver state
    grh = GameRuleHypothesis(
        rule_description="shared rule",
        action_semantics={},
        objective_description="",
        level_strategy="",
        confidence=0.9,
        evidence=[],
        contradictions=[],
        source="llm"
    )
    orch.solve_engine._game_rule_hypotheses = [grh]
    
    # Property should return the one from solver
    assert orch._game_rule_hypothesis == grh
    
    # Update solver, property should reflect it
    grh2 = GameRuleHypothesis(rule_description="new rule", action_semantics={}, objective_description="", level_strategy="", confidence=0.5, evidence=[], contradictions=[], source="llm")
    orch.solve_engine._game_rule_hypotheses = [grh2]
    assert orch._game_rule_hypothesis == grh2

@pytest.mark.asyncio
async def test_solve_triggers_grh_sync_and_flush():
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
    
    # Mock game rule hypothesizer to return a new GRH
    grh = GameRuleHypothesis(rule_description="r", action_semantics={}, objective_description="", level_strategy="", confidence=0.9, evidence=[], contradictions=[], source="llm")
    engine.game_rule_hypothesizer.hypothesize = AsyncMock(return_value=[grh])
    
    # Set current_level=0 and step=0 to trigger B151
    engine._current_level = 0
    
    obs = {
        "grid": [[0]], 
        "task_id": "t1", 
        "dataset_id": "d1", 
        "available_actions": ["A1"],
        "training_examples": [{"input": [[0]], "output": [[1]]}]
    }
    ctx = {"last_transition_effect": {"reward_signal": 0.0}}
    
    # Need level_pattern to trigger B151
    from agents.arc3.grid_analysis import LevelPattern
    lp = LevelPattern({}, {}, None, "summary", 0.9, 1)
    
    await engine.solve(obs, ctx, step=0, state_graph=MagicMock(), current_state_hash="h1", level_pattern=lp, solved_levels=[{}])
    
    # Should have called load (at start) and flush (at end)
    # execute_read was for _sync_grh_from_db
    assert mock_graph.db.execute_read.called
    # execute_write was for _flush_grh_writes
    assert mock_graph.db.execute_write.called
