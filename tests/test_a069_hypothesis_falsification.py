import pytest
from unittest.mock import MagicMock
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.solver import Hypothesis, HypothesisWorkspace, HypothesisStatus

@pytest.fixture
def orchestrator():
    brain = MagicMock()
    llm = MagicMock()
    serializer = MagicMock()
    config = {"macro_executor": {"enabled": True}}
    orch = ARCOrchestrator(
        brain_client=brain,
        llm_client=llm,
        session_id="test-session",
        serializer=serializer,
        config=config
    )
    return orch

def test_hypothesis_falsification_terminal_stall(orchestrator):
    # Setup workspace
    workspace = HypothesisWorkspace()
    action_id = "ACTION1"
    h = Hypothesis(
        id=f"action-{action_id}",
        scope="action-causality",
        statement=f"{action_id} moves things",
        confidence=0.5,
        status=HypothesisStatus.ACTIVE
    )
    workspace.add_hypothesis(h)
    
    orchestrator._solve_context = {"hypothesis_workspace": workspace}
    
    # Setup history with 3 stalls for ACTION1
    orchestrator._step_history = [
        {"action_id": action_id, "reward_components": {"meaningful_progress": False}},
        {"action_id": action_id, "reward_components": {"meaningful_progress": False}},
        {"action_id": action_id, "reward_components": {"meaningful_progress": False}}
    ]
    
    # Trigger workspace update
    orchestrator._update_hypothesis_workspace(step=3)
    
    # Check demotion
    assert h.confidence < 0.5
    # If confidence drops below 0.25 it should be demoted
    # 0.5 * 0.5 = 0.25. Our logic uses h.confidence < 0.25 for demotion.
    # Let's check if it dropped to 0.25
    assert h.confidence == 0.25
    assert "terminal stall" in h.evidence_against[0]

def test_hypothesis_falsification_non_monotonic_object_progress(orchestrator):
    # Setup workspace
    workspace = HypothesisWorkspace()
    h = Hypothesis(
        id="vc-test",
        scope="victory-condition",
        statement="Win by doing X",
        confidence=0.5,
        status=HypothesisStatus.ACTIVE
    )
    workspace.add_hypothesis(h)
    
    orchestrator._solve_context = {"hypothesis_workspace": workspace}
    
    # Setup history with declining object progress
    orchestrator._step_history = [
        {"action_id": "ACTION1", "object_progress": {"score": 5.0}},
        {"action_id": "ACTION1", "object_progress": {"score": 3.0}}
    ]
    
    # Trigger workspace update
    orchestrator._update_hypothesis_workspace(step=2)
    
    # Check confidence drop
    assert h.confidence < 0.5
    assert "non-monotonic object progress" in h.evidence_against[0]
