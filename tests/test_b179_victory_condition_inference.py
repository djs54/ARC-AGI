import pytest
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.solver import SolveEngine, GameArchetype, VictoryCondition, VictoryType, VictoryHypothesizer

@pytest.fixture
def engine():
    brain = MagicMock()
    brain.register_plan = AsyncMock(return_value={"plan_id": "p1"})
    brain.recall_plans = AsyncMock(return_value={"plans": []})
    brain.recall_relevant_lessons = AsyncMock(return_value={"lessons": []})
    brain.analogical_search = AsyncMock(return_value={"results": []})
    
    llm = MagicMock()
    llm.achat = AsyncMock(return_value='{"condition_type":"reach_goal","description":"test","confidence":0.7}')
    
    eng = SolveEngine(brain, llm, "session-1")
    eng._archetype = GameArchetype.SPACE
    eng._archetype_locked = True
    return eng

@pytest.mark.asyncio
async def test_inference_fires_at_new_threshold(engine):
    # New threshold is 0.45. Confidence 0.5 should trigger.
    engine._archetype_confidence = 0.5
    
    obs = {"grid": [[0]], "task_id": "t1", "dataset_id": "d1", "available_actions": ["A1"]}
    ctx = {"last_transition_effect": {"reward_signal": 0.0}}
    
    await engine.solve(obs, ctx, step=0, state_graph=MagicMock(), current_state_hash="h1")
    
    assert engine._victory_condition is not None
    assert engine._last_victory_attempt_step == 0

@pytest.mark.asyncio
async def test_inference_does_not_fire_below_threshold(engine):
    # Confidence 0.3 < 0.45. Should not trigger at step 0.
    engine._archetype_confidence = 0.3
    
    obs = {"grid": [[0]], "task_id": "t1", "dataset_id": "d1", "available_actions": ["A1"]}
    ctx = {"last_transition_effect": {"reward_signal": 0.0}}
    
    await engine.solve(obs, ctx, step=0, state_graph=MagicMock(), current_state_hash="h1")
    
    assert engine._victory_condition is None
    assert engine._last_victory_attempt_step == -100

@pytest.mark.asyncio
async def test_step_based_fallback_trigger(engine):
    # Confidence 0.3 < 0.45. But step 15 should trigger.
    engine._archetype_confidence = 0.3
    
    obs = {"grid": [[0]], "task_id": "t1", "dataset_id": "d1", "available_actions": ["A1"]}
    ctx = {"last_transition_effect": {"reward_signal": 0.0}}
    
    await engine.solve(obs, ctx, step=15, state_graph=MagicMock(), current_state_hash="h1")
    
    assert engine._victory_condition is not None
    assert engine._last_victory_attempt_step == 15

@pytest.mark.asyncio
async def test_zero_progress_trigger(engine):
    # Confidence 0.3 < 0.45. But streak 5 should trigger.
    engine._archetype_confidence = 0.3
    engine._reward_history = [0.0] * 5
    
    obs = {"grid": [[0]], "task_id": "t1", "dataset_id": "d1", "available_actions": ["A1"]}
    ctx = {"last_transition_effect": {"reward_signal": 0.0}}
    
    await engine.solve(obs, ctx, step=5, state_graph=MagicMock(), current_state_hash="h1")
    
    assert engine._victory_condition is not None
    assert engine._last_victory_attempt_step == 5

@pytest.mark.asyncio
async def test_cooldown_prevents_repeated_calls(engine):
    engine._archetype_confidence = 0.5
    obs = {"grid": [[0]], "task_id": "t1", "dataset_id": "d1", "available_actions": ["A1"]}
    ctx = {"last_transition_effect": {"reward_signal": 0.0}}
    
    # First call at step 0
    await engine.solve(obs, ctx, step=0, state_graph=MagicMock(), current_state_hash="h1")
    assert engine._last_victory_attempt_step == 0
    
    # Clear VC to see if it triggers again
    engine._victory_condition = None
    
    # Second call at step 5 (cooldown is 10)
    await engine.solve(obs, ctx, step=5, state_graph=MagicMock(), current_state_hash="h1")
    assert engine._last_victory_attempt_step == 0 # Still 0, didn't update
    assert engine._victory_condition is None
    
    # Third call at step 10
    await engine.solve(obs, ctx, step=10, state_graph=MagicMock(), current_state_hash="h1")
    assert engine._last_victory_attempt_step == 10
    assert engine._victory_condition is not None

@pytest.mark.asyncio
async def test_regression_high_confidence_still_fires(engine):
    # Confidence 0.8 > 0.65 (old threshold) and > 0.45 (new)
    engine._archetype_confidence = 0.8
    
    obs = {"grid": [[0]], "task_id": "t1", "dataset_id": "d1", "available_actions": ["A1"]}
    ctx = {"last_transition_effect": {"reward_signal": 0.0}}
    
    await engine.solve(obs, ctx, step=0, state_graph=MagicMock(), current_state_hash="h1")
    
    assert engine._victory_condition is not None
    assert engine._last_victory_attempt_step == 0
