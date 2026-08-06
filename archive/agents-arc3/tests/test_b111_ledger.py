
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from benchmarks.arc3.adapter import ARC3Adapter, NoOpBrainClient, LedgerBrainClient
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.runner import DurableARCRunner
from benchmarks.arc3.state_serializer import StateSerializerForARC

@pytest.mark.asyncio
async def test_ledger_brain_client():
    inner = MagicMock()
    inner.notify_turn = AsyncMock(return_value={"status": "ok"})
    ledger = []
    client = LedgerBrainClient(inner, ledger, lambda: 1)
    client.current_phase = "test"
    
    await client.notify_turn(role="user", content="hello", session_id="s1")
    assert len(ledger) == 1
    assert ledger[0]["call_type"] == "notify_turn"
    assert ledger[0]["phase"] == "test"
    assert ledger[0]["step"] == 1

@pytest.mark.asyncio
async def test_runner_ledger_aggregation():
    # Setup mock harness and brain
    harness = MagicMock()
    harness.llm_client = MagicMock()
    harness.llm_client.chat.return_value = '{"action_id": "ACTION1", "rationale": "test"}'
    harness.serializer = StateSerializerForARC()
    harness.config.parameters = {"max_attempts_per_puzzle": 1}
    harness.mock_api = True
    
    brain = MagicMock()
    brain.db = MagicMock()
    brain.db.execute_read = AsyncMock(return_value=[])
    brain.db.execute_write = AsyncMock()
    brain.branch_quest = AsyncMock(return_value={"side_quest_id": "sq1"})
    brain.notify_turn = AsyncMock(return_value={"status": "ok"})
    brain.current_truth = AsyncMock(return_value={"results": []})
    brain.recall_relevant_lessons = AsyncMock(return_value={"lessons": []})
    brain.analogical_search = AsyncMock(return_value={"results": []})
    brain.recall_plans = AsyncMock(return_value={"plans": []})
    brain.register_plan = AsyncMock(return_value={"plan_id": "p1"})
    brain.report_outcome = AsyncMock(return_value={"status": "ok"})
    
    # We want to use real ingest_api_knowledge to see its calls
    # but we need to patch its import in api_knowledge.py to avoid 
    # Side effects if any. Actually api_knowledge.py just has chunks and a function.
    
    runner = DurableARCRunner(harness, brain, {
        "llm": {"model": "test-model"},
        "max_retries_per_puzzle": 1,
        "cost": {"budget_per_puzzle_usd": 10.0}
    })
    
    # Mocking these to return enough values
    runner._initial_frame = AsyncMock(return_value=({"frame": [[0]], "state": "WIN"}, "g1"))
    runner._execute_action = AsyncMock(return_value=({"frame": [[0]], "state": "WIN"}, 1.0, True, "g1"))
    
    task = MagicMock()
    task.task_id = "t1"
    task.game_id = "g1"
    
    with patch("agents.arc3.runner.CheckpointManager") as mock_mgr_cls, \
         patch("agents.arc3.entity_graph.EntityGraphBuilder") as mock_eg_cls, \
         patch("benchmarks.arc3.adapter.LedgerBrainClient.branch_quest", new_callable=AsyncMock) as mock_branch:
        
        mock_branch.return_value = {"side_quest_id": "sq1"}
        mock_eg = mock_eg_cls.return_value
        mock_eg.db = MagicMock()
        mock_eg.db.execute_read = AsyncMock(return_value=[])
        mock_eg.db.execute_write = AsyncMock()
        mock_eg.bootstrap = AsyncMock(return_value={"n_entities": 0})
        mock_eg.record_action_effect = AsyncMock()
        mock_eg.get_entity_roles = AsyncMock(return_value={})
        
        mock_mgr = mock_mgr_cls.return_value
        mock_checkpoint = MagicMock()
        mock_checkpoint.tasks = {}
        mock_mgr.load_or_create.return_value = mock_checkpoint
        
        results = await runner.run([task], "card-1")
        
        assert len(results) == 1
        assert "sidequests_ledger" in results[0]
        
        ledger = results[0]["sidequests_ledger"]
        assert len(ledger) > 0
        
        call_types = [e["call_type"] for e in ledger]
        # It will be 'notify_turn' because ingest_api_knowledge calls notify_turn
        assert "notify_turn" in call_types
        assert "branch_quest" in call_types
        
        phases = [e["phase"] for e in ledger]
        assert "bootstrap" in phases
