import pytest
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.solver import SolveEngine, GameArchetype, RoleType, ObjectRole, VictoryCondition, VictoryType

@pytest.fixture
def engine():
    brain = MagicMock()
    llm = MagicMock()
    return SolveEngine(brain, llm, "session-1")

def test_exploration_bonus_during_plateau(engine):
    # Setup hypothesis context with sustained plateau
    # Need consecutive_zero_reward_steps >= 5 for bonus
    engine._reward_history = [0.0] * 5
    
    context = {
        "observed_action_effects": [
            {"action": "ACTION1", "avg_meaningful_change": 0.1, "zero_reward_streak": 5}
        ],
        "consecutive_zero_reward_steps": 5
    }
    available = ["ACTION1", "ACTION2"] # ACTION2 is untried
    
    scores = engine._score_action_families(context, available)
    
    # ACTION1: avg_change * 0.5 - streak * 0.25 = 0.1*0.5 - 5*0.25 = 0.05 - 1.25 = -1.20
    # ACTION2: untried, should get bonus 0.3 + min(5*0.02, 0.2) = 0.3 + 0.1 = 0.4
    assert scores["ACTION2"] >= 0.3
    assert scores["ACTION2"] > scores["ACTION1"]

def test_no_exploration_bonus_outside_plateau(engine):
    engine._reward_history = [0.0] * 2 # Short streak
    
    context = {
        "observed_action_effects": [],
        "consecutive_zero_reward_streak": 2
    }
    available = ["ACTION1"]
    
    scores = engine._score_action_families(context, available)
    assert scores["ACTION1"] == 0.0 # No bonus

def test_accelerated_penalty_for_long_streaks(engine):
    engine._reward_history = [0.0] * 10
    
    context = {
        "observed_action_effects": [
            {"action": "ACTION1", "avg_meaningful_change": 0.0, "zero_reward_streak": 10}
        ],
        "consecutive_zero_reward_steps": 10
    }
    available = ["ACTION1"]
    
    scores = engine._score_action_families(context, available)
    # Base penalty: 10 * 0.25 = 2.5
    # Accelerated penalty: 1.0
    # Total: -3.5
    assert scores["ACTION1"] <= -3.5

@pytest.mark.asyncio
async def test_plateau_lock_threshold_decay(engine):
    # Setup grounded entities to trigger plateau
    engine._object_roles = {
        1: ObjectRole(color_id=1, role=RoleType.PLAYER, confidence=0.9),
        2: ObjectRole(color_id=2, role=RoleType.GOAL, confidence=0.9)
    }
    engine._reward_history = [0.0] * 10
    
    # Mock brain tools to avoid real calls
    engine.brain.register_plan = AsyncMock(return_value={"plan_id": "p1"})
    engine.brain.recall_plans = AsyncMock(return_value={"plans": []})
    engine.brain.recall_relevant_lessons = AsyncMock(return_value={"lessons": []})
    
    # Step 1: Initial lock on ACTION1
    obs = {"grid": [[0]], "task_id": "t1", "dataset_id": "d1", "available_actions": ["ACTION1", "ACTION2"]}
    ctx = {
        "observed_action_effects": [{"action": "ACTION1", "avg_meaningful_change": 2.0, "zero_reward_streak": 0}],
        "consecutive_zero_reward_steps": 10,
        "last_transition_effect": {"reward_signal": 0.0, "meaningful_change_score": 1.0},
        # A032: clear the B215 MIN_DISTINCT gate (solver.py:3076) that requires
        # >= 3 distinct tried families before plateau mode activates. The test's
        # intent is to exercise the lock-threshold decay, not the entry gate.
        "action_coverage": {"tested_count": 3},
    }
    # ACTION1 score: 2.0 * 0.5 = 1.0
    # ACTION2 score: curiosity bonus = 0.5
    
    await engine.solve(obs, ctx, step=10, state_graph=MagicMock(), current_state_hash="h1")
    assert engine._plateau_locked_family == "ACTION1"
    
    # Step 2: Decrease ACTION1 score so ACTION2 becomes best_candidate, but below threshold
    ctx["observed_action_effects"][0]["avg_meaningful_change"] = 0.4
    # ACTION1 score: 0.4 * 0.5 = 0.2
    # ACTION2 score: 0.5
    # Diff = 0.3. Threshold starts at 0.5.
    
    # Advance steps to decay threshold
    # step 11: duration=1, threshold=0.45. 0.3 < 0.45
    # step 12: duration=2, threshold=0.40. 0.3 < 0.40
    # step 13: duration=3, threshold=0.35. 0.3 < 0.35
    # step 14: duration=4, threshold=0.30. 0.3 < 0.30? No, best_score > current_score + threshold
    # 0.5 > 0.2 + 0.3 is FALSE (0.5 > 0.5 is false)
    # step 15: duration=5, threshold=0.25. 0.5 > 0.2 + 0.25 -> 0.5 > 0.45 TRUE.
    
    for i in range(11, 15):
        await engine.solve(obs, ctx, step=i, state_graph=MagicMock(), current_state_hash="h1")
        assert engine._plateau_locked_family == "ACTION1", f"Failed at step {i}"
        
    # Step 15 should trigger unlock
    await engine.solve(obs, ctx, step=15, state_graph=MagicMock(), current_state_hash="h1")
    
    assert engine._plateau_locked_family == "ACTION2"
    assert engine._plateau_lock_duration == 0
