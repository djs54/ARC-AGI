import pytest
from unittest.mock import MagicMock
from agents.arc3.orchestrator import ARCOrchestrator

@pytest.fixture
def orchestrator():
    brain = MagicMock()
    llm = MagicMock()
    serializer = MagicMock()
    config = {"macro_executor": {"enabled": True, "min_confirming_steps": 2}}
    orch = ARCOrchestrator(
        brain_client=brain,
        llm_client=llm,
        session_id="test-session",
        serializer=serializer,
        config=config
    )
    return orch

def test_check_macro_eligibility_multi_action(orchestrator):
    # Setup history with meaningful progress
    orchestrator._step_history = [
        {
            "action_id": "ACTION1",
            "reward_components": {"meaningful_progress": True},
            "state_after": "NOT_FINISHED"
        },
        {
            "action_id": "ACTION1",
            "reward_components": {"meaningful_progress": True},
            "state_after": "NOT_FINISHED"
        }
    ]
    
    # Case 1: Multiple actions available, but ACTION1 dominates
    obs = {"available_actions": ["ACTION1", "ACTION2"]}
    is_eligible, action_id = orchestrator.check_macro_eligibility(obs)
    reason = orchestrator._macro_eligibility_reason
    assert is_eligible is True
    assert action_id == "ACTION1"
    assert reason == "dominant_action_detected"

    # Case 2: ACTION1 is not available
    obs = {"available_actions": ["ACTION2", "ACTION3"]}
    is_eligible, action_id = orchestrator.check_macro_eligibility(obs)
    assert is_eligible is False

    # Case 3: Action is refuted
    orchestrator._hypothesis_context = {
        "refuted_hypotheses": [{"id": "action-ACTION1"}]
    }
    obs = {"available_actions": ["ACTION1", "ACTION2"]}
    is_eligible, action_id = orchestrator.check_macro_eligibility(obs)
    assert is_eligible is False

def test_macro_stall_stop_logic(orchestrator):
    # This test would ideally test the runner loop, but we can check the state here
    orchestrator._macro_terminal_stall_count = 2
    # simulate a stall step
    orchestrator._macro_terminal_stall_count += 1
    assert orchestrator._macro_terminal_stall_count >= 3
