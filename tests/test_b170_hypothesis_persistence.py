import pytest
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.hypothesis import HypothesisManager, Hypothesis

@pytest.mark.asyncio
async def test_hypothesis_persistence():
    db = MagicMock()
    db.execute_write = AsyncMock()
    db.execute_read = AsyncMock(return_value=[
        {
            "h.id": "h1",
            "h.description": "desc1",
            "h.category": "action_semantic",
            "h.confidence": 0.8,
            "h.status": "confirmed",
            "h.evidence_count": 5
        }
    ])
    
    mgr = HypothesisManager(MagicMock(), "session-1")
    mgr._task_id = "task-1"
    
    # Use a dummy object that looks like EntityGraphBuilder
    mock_eg = MagicMock()
    mock_eg.db = db
    mgr._entity_graph = mock_eg
    
    # 1. Persist
    h = Hypothesis(id="h1", description="desc1", category="action_semantic", confidence=0.8, status="confirmed", support_count=5)
    await mgr.persist_hypothesis(h)
    
    assert db.execute_write.called
    # Check MERGE Hypothesis query
    write_calls = [c[0][0] for c in db.execute_write.call_args_list]
    assert any("MERGE (h:Hypothesis {id: $id})" in q for q in write_calls)
    assert any("MERGE (h)-[:HYPOTHESIZED_IN]->(s)" in q for q in write_calls)
    
    # 2. Load
    mgr.hypotheses.clear()
    count = await mgr.load_hypotheses()
    assert count == 1
    assert "h1" in mgr.hypotheses
    assert mgr.hypotheses["h1"].description == "desc1"
    assert mgr.hypotheses["h1"].confidence == 0.8
    assert mgr.hypotheses["h1"].status == "confirmed"

@pytest.mark.asyncio
async def test_hypothesis_manager_observe_async():
    # Verify that observe() is now async and calls persist_hypothesis
    mgr = HypothesisManager(MagicMock(), "session-1")
    mgr._task_id = "task-1"
    mgr.persist_hypothesis = AsyncMock()
    
    grid = [[0, 1], [1, 0]]
    observation = {"grid": grid, "colors": [], "state": "NOT_FINISHED"}
    
    # Step 0: Initial observe
    await mgr.observe(
        grid=grid,
        action_taken=None,
        step=0,
        available_actions=["ACTION1"],
        observation=observation
    )
    
    # Step 1: Transition observe
    await mgr.observe(
        grid=grid,
        action_taken="ACTION1",
        step=1,
        available_actions=["ACTION1"],
        observation=observation,
        transition_meta={"reward": 0.0}
    )
    
    # Should have called persist_hypothesis
    assert mgr.persist_hypothesis.called
    # Check that it was called with action hypothesis (id might be action-ACTION1)
    called_ids = [c[0][0].id for c in mgr.persist_hypothesis.call_args_list]
    assert any(hid.startswith("action-") for hid in called_ids)
