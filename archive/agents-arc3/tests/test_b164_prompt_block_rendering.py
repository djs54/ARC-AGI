
import pytest
from unittest.mock import MagicMock
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.solver import ObjectRole, RoleType

def test_observation_block_color_labeling():
    orchestrator = ARCOrchestrator(MagicMock(), MagicMock(), "session-1", MagicMock(), {})
    
    # Setup grounded roles
    player = ObjectRole(color_id=3, role=RoleType.PLAYER, confidence=1.0)
    goal = ObjectRole(color_id=11, role=RoleType.GOAL, confidence=1.0)
    orchestrator._solve_context = {
        "object_roles": {3: player, 11: goal}
    }
    
    observation = {
        "grid": [[5, 5], [5, 5]], # All color 5 (background)
        "colors": [
            {"value": 5, "count": 4},
            {"value": 3, "count": 0},
            {"value": 11, "count": 0}
        ],
        "frame_hash": "abc"
    }
    
    rendered = orchestrator._format_observation_section(observation)
    
    # Before A013, color 5 would be labeled as goal because it has the highest count
    # Now, only cid 11 should be labeled as goal
    assert "5:4" in rendered
    assert "5:4(goal)" not in rendered
    assert "3:0(player)" in rendered
    assert "11:0(goal)" in rendered

def test_history_block_collapsing():
    orchestrator = ARCOrchestrator(MagicMock(), MagicMock(), "session-1", MagicMock(), {})
    orchestrator.MAX_PROMPT_HISTORY = 10
    
    history = [
        {"step": 1, "action_id": "ACTION1", "rationale": "first step", "reward": 0.0},
        {"step": 2, "action_id": "ACTION6", "rationale": "repeating", "reward": 0.0},
        {"step": 3, "action_id": "ACTION6", "rationale": "repeating", "reward": 0.0},
        {"step": 4, "action_id": "ACTION6", "rationale": "repeating", "reward": 0.0},
        {"step": 5, "action_id": "ACTION2", "rationale": "new move", "reward": 1.0},
    ]
    
    rendered = orchestrator._format_history_section(history)
    
    assert "Step 1 → ACTION1 (first step) · reward 0.00" in rendered
    assert "Steps 2–4 → ACTION6 ×3 (repeating) · reward 0.00" in rendered
    assert "Step 5 → ACTION2 (new move) · reward 1.00" in rendered

def test_action_fact_block_prioritization():
    orchestrator = ARCOrchestrator(MagicMock(), MagicMock(), "session-1", MagicMock(), {})
    
    hyp_ctx = {
        "action_facts": [
            {"action": "ACTION1", "fact_type": "localized_change", "consistency": 0.5, "evidence_count": 1, "description": "low priority", "value_status": "low_value"},
            {"action": "ACTION6", "fact_type": "deterministic_effect", "consistency": 1.0, "evidence_count": 5, "description": "high priority", "value_status": "high_value"},
        ]
    }
    
    rendered_lines = orchestrator._format_action_fact_section(hyp_ctx)
    
    # ACTION6 should be first due to deterministic_effect and higher consistency/evidence
    assert "ACTION6" in rendered_lines[0]
    assert "ACTION1" in rendered_lines[1]
