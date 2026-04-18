
import pytest
from unittest.mock import MagicMock
from agents.arc3.solver import PlanChunker, ObjectRole

def test_graduation_saturated_low_evidence():
    # Setup a scenario matching the problematic run
    # single action available, all tested, high geometry confidence, but zero reward decay
    chunker = PlanChunker()
    
    player_role = ObjectRole(color_id=3, confidence=0.80, estimated_position={"row": 10, "col": 10})
    goal_role = ObjectRole(color_id=11, confidence=0.86, estimated_position={"row": 20, "col": 20})
    
    # Context with 1 action available and tested
    hypothesis_context = {
        "action_coverage": {
            "tested_count": 1,
            "untested_count": 0,
            "initial_exploration_complete": True,
            "top_two_low_value": True # One action is enough to trigger this if it's low value
        },
        "action_facts": [
            {"action": "ACTION6", "fact_type": "localized_change", "value_status": "low_value"}
        ],
        "loop_detected": False
    }
    
    # 10 steps of zero reward
    result = chunker._graduation_assessment(
        player_role=player_role,
        goal_role=goal_role,
        hypothesis_context=hypothesis_context,
        available_actions=["ACTION6"],
        consecutive_zero_reward_steps=10,
        steps_using_chunk=10
    )
    
    # CURRENT BEHAVIOR (Failing A010): stays in explore due to score decay and evidence floor
    # We want it to be READY
    assert result["ready"] == True, f"Should graduate when coverage is saturated. Reason: {result['reason']}"

def test_graduation_normal_multi_action_stalls():
    # Multi-action game where we haven't explored everything - should still stay in explore
    chunker = PlanChunker()
    
    player_role = ObjectRole(color_id=3, confidence=0.80, estimated_position={"row": 10, "col": 10})
    goal_role = ObjectRole(color_id=11, confidence=0.86, estimated_position={"row": 20, "col": 20})
    
    hypothesis_context = {
        "action_coverage": {
            "tested_count": 1,
            "untested_count": 5,
            "initial_exploration_complete": False,
            "top_two_low_value": False
        },
        "action_facts": [],
        "loop_detected": False
    }
    
    result = chunker._graduation_assessment(
        player_role=player_role,
        goal_role=goal_role,
        hypothesis_context=hypothesis_context,
        available_actions=["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5", "ACTION6"],
        consecutive_zero_reward_steps=5
    )
    
    assert result["ready"] == False
