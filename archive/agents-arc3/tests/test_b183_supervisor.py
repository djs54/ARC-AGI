import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.supervisor import PuzzleSupervisor, SupervisorDecision, SupervisorVerdict

@pytest.fixture
def supervisor():
    return PuzzleSupervisor(check_interval=5)

@pytest.mark.asyncio
async def test_supervisor_continue_interval(supervisor):
    # Step 1 is not a multiple of 5
    history = [{"step": 1}]
    verdict = await supervisor.evaluate(history, [])
    assert verdict.decision == SupervisorDecision.CONTINUE
    assert "not in check interval" in verdict.reason

@pytest.mark.asyncio
async def test_supervisor_oscillation_detection(supervisor):
    # 8 steps of oscillating between two frame hashes
    history = []
    for i in range(10):
        frame_hash = "hash_a" if i % 2 == 0 else "hash_b"
        history.append({"step": i, "frame_hash": frame_hash, "reward": 0.0})
    
    # Evaluate at step 10 (multiple of 5)
    verdict = await supervisor.evaluate(history, [])
    assert verdict.decision == SupervisorDecision.RESET_STRATEGY
    assert "oscillating" in verdict.reason

@pytest.mark.asyncio
async def test_supervisor_abandon_detection(supervisor):
    # 30 steps of zero reward
    history = [{"step": i, "reward": 0.0} for i in range(30)]
    
    verdict = await supervisor.evaluate(history, [])
    assert verdict.decision == SupervisorDecision.ABANDON
    assert "consecutive zero-reward" in verdict.reason

@pytest.mark.asyncio
async def test_supervisor_nudge_diversity(supervisor):
    # 10 steps using only ACTION1 when many are available
    history = []
    for i in range(10):
        history.append({
            "step": i, 
            "action_id": "ACTION1", 
            "reward": 0.0,
            "next_observation": {"available_actions": ["ACTION1", "ACTION2", "ACTION3", "ACTION4"]}
        })
    
    verdict = await supervisor.evaluate(history, [])
    assert verdict.decision == SupervisorDecision.NUDGE
    assert "diversity" in verdict.reason
    assert "ACTION2" in verdict.nudge_hint

@pytest.mark.asyncio
async def test_supervisor_budget_warning(supervisor):
    # History length must be multiple of 5 (check_interval)
    history = [{"step": i, "reward": 0.0} for i in range(5)]
    cost = MagicMock()
    cost.total_cost_usd = 0.8
    cost.budget_usd = 1.0
    
    verdict = await supervisor.evaluate(history, [], cost_tracker=cost)
    assert verdict.decision == SupervisorDecision.NUDGE
    assert "budget high" in verdict.reason

@pytest.mark.asyncio
async def test_supervisor_centroid_stuck(supervisor):
    # 10 steps with same player pos
    history = []
    for i in range(10):
        history.append({
            "step": i, 
            "autopilot_player_row": 5.0, 
            "autopilot_player_col": 5.0,
            "reward": 0.0
        })
    
    verdict = await supervisor.evaluate(history, [])
    assert verdict.decision == SupervisorDecision.RESET_STRATEGY
    assert "position unchanged" in verdict.reason

@pytest.mark.asyncio
async def test_supervisor_llm_escalation(supervisor):
    # Set up ambiguous state: 20 steps zero reward, no victory condition
    history = []
    for i in range(25):
        history.append({
            "step": i, 
            "reward": 0.0,
            "solve_context": {"victory_condition": None}
        })
    
    # Add LLM client
    mock_llm = MagicMock()
    mock_llm.achat = AsyncMock(return_value='{"decision": "abandon", "reason": "too expensive", "nudge_hint": null}')
    supervisor.llm = mock_llm
    
    verdict = await supervisor.evaluate(history, [])
    assert verdict.decision == SupervisorDecision.ABANDON
    assert "LLM Judge" in verdict.reason
    assert mock_llm.achat.called
