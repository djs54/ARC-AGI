
import pytest
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.orchestrator import ARCOrchestrator

@pytest.mark.asyncio
async def test_orchestrator_retrieval_gating():
    brain = AsyncMock()
    # Default responses
    brain.current_truth.return_value = {"results": []}
    brain.recall_relevant_lessons.return_value = {"lessons": []}
    brain.analogical_search.return_value = {"results": []}
    
    config = {"llm": {"model": "test"}}
    orchestrator = ARCOrchestrator(brain, MagicMock(), "session-1", MagicMock(), config)
    
    observation = {"dataset_id": "d", "task_id": "t", "grid": [[0]]}
    
    # Mock _should_trigger_retrieval to always return True for this test
    orchestrator._should_trigger_retrieval = MagicMock(return_value=True)
    
    # 1. First call (step 0, bootstrap) -> should NOT gate
    await orchestrator.perceive(observation, step=0)
    assert brain.current_truth.call_count == 1
    
    # 2. Second call at step 1
    # Mock _retrieval_needed_for_prompt to return False (gated)
    orchestrator._retrieval_needed_for_prompt = MagicMock(return_value=False)
    await orchestrator.perceive(observation, step=1)
    # Should still be 1 if gated
    assert brain.current_truth.call_count == 1
    
    # 3. Third call at step 2, still gated
    await orchestrator.perceive(observation, step=2)
    assert brain.current_truth.call_count == 1
    
    # 4. Fourth call at step 3, NOT gated but same query as step 0 (dedup)
    orchestrator._retrieval_needed_for_prompt = MagicMock(return_value=True)
    # _memory_query is deterministic for same observation
    await orchestrator.perceive(observation, step=3)
    # Should still be 1 if deduped (step 3 - step 0 = 3, default N=1? 
    # Actually my implementation uses (step - last_step) <= 1.
    # So step 3 vs step 0 will NOT dedup.
    # Let's test step 1 as NOT gated.
    
    # Reset for a cleaner test of N=1
    brain.current_truth.reset_mock()
    orchestrator._last_retrieval_kind_fingerprint.clear()
    
    # Step 10: Retrieve
    orchestrator._retrieval_needed_for_prompt = MagicMock(return_value=True)
    await orchestrator.perceive(observation, step=10)
    assert brain.current_truth.call_count == 1
    
    # Step 11: Same query -> Dedup
    await orchestrator.perceive(observation, step=11)
    assert brain.current_truth.call_count == 1
    
    # Step 12: Same query -> Dedup (since last call/hit was step 11)
    await orchestrator.perceive(observation, step=12)
    assert brain.current_truth.call_count == 1
    
    # Step 13: Different query -> Call
    new_obs = {"dataset_id": "d", "task_id": "t", "grid": [[1, 2], [3, 4]]}
    await orchestrator.perceive(new_obs, step=13)
    assert brain.current_truth.call_count == 2


@pytest.mark.asyncio
async def test_orchestrator_memory_degraded_throttles_retrieval():
    brain = AsyncMock()
    brain.current_truth.return_value = {"results": []}
    brain.recall_relevant_lessons.return_value = {"lessons": []}
    brain.analogical_search.return_value = {"results": []}
    brain.memory_degraded = True
    brain.memory_degraded_reason = "daemon_offline"

    orchestrator = ARCOrchestrator(brain, MagicMock(), "session-1", MagicMock(), {"llm": {"model": "test"}})
    observation = {"dataset_id": "d", "task_id": "t", "grid": [[0]], "available_actions": ["ACTION1"]}

    assert orchestrator._should_trigger_retrieval(observation, step=1) is False
    assert orchestrator._should_trigger_retrieval(observation, step=10) is False
