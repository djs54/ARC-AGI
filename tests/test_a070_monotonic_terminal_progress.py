import pytest
from unittest.mock import MagicMock
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.solver import SolveContext, TerminalGroundedScore, RoleType

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
    # Setup solve context with roles
    ctx = SolveContext()
    ctx.object_roles = {
        1: {"role": RoleType.PLAYER},
        2: {"role": RoleType.GOAL}
    }
    orch._solve_context = ctx
    return orch

def test_monotonic_distance_reduction(orchestrator):
    # Step 1
    obs1 = {
        "grid": [[1, 0, 0, 2]], # dist = 3
        "levels_completed": 0
    }
    record1 = {"reward": 0.0}
    orchestrator._step_history.append(record1)
    orchestrator._update_terminal_progress(obs1, record1)
    
    # Step 2
    obs2 = {
        "grid": [[0, 1, 0, 2]], # dist = 2
        "levels_completed": 0
    }
    record2 = {"reward": 0.0}
    orchestrator._step_history.append(record2)
    orchestrator._update_terminal_progress(obs2, record2)
    
    # Step 3
    obs3 = {
        "grid": [[0, 0, 1, 2]], # dist = 1
        "levels_completed": 0
    }
    record3 = {"reward": 0.0}
    orchestrator._step_history.append(record3)
    orchestrator._update_terminal_progress(obs3, record3)
    
    ts = orchestrator._solve_context.terminal_score
    assert ts.trend == "improving"
    assert ts.monotonicity == 1.0
    assert record3["terminal_progress_trend"] == "improving"

def test_oscillation_detection(orchestrator):
    # Step 1: dist 3
    obs1 = {"grid": [[1, 0, 0, 2]], "levels_completed": 0}
    record1 = {"reward": 0.0}
    orchestrator._step_history.append(record1)
    orchestrator._update_terminal_progress(obs1, record1)
    
    # Step 2: dist 2 (improvement)
    obs2 = {"grid": [[0, 1, 0, 2]], "levels_completed": 0}
    record2 = {"reward": 0.0}
    orchestrator._step_history.append(record2)
    orchestrator._update_terminal_progress(obs2, record2)
    
    # Step 3: dist 3 (regression)
    obs3 = {"grid": [[1, 0, 0, 2]], "levels_completed": 0}
    record3 = {"reward": 0.0}
    orchestrator._step_history.append(record3)
    orchestrator._update_terminal_progress(obs3, record3)
    
    ts = orchestrator._solve_context.terminal_score
    assert ts.trend == "oscillating"
    assert ts.oscillation_penalty > 0
