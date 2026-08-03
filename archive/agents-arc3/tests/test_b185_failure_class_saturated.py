
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from agents.arc3.runner import DurableARCRunner
from agents.arc3.failure_taxonomy import FailureTaxonomy
from agents.arc3.phase import SolvePhase

class MockTask:
    def __init__(self, task_id):
        self.task_id = task_id
        self.game_id = "test-game"
        self.reference_solution = None

@pytest.mark.asyncio
async def test_failure_class_coverage_saturated_abort():
    harness = MagicMock()
    harness.mock_api = True
    harness.config.parameters = {"max_attempts_per_puzzle": 1}
    harness._get_mock_initial_frame.return_value = {"frame": [[0, 0], [0, 0]], "game_id": "test-game", "state": "NOT_STARTED"}
    harness.serializer = MagicMock()
    harness.serializer._estimate_tokens.return_value = 10
    
    brain = AsyncMock()
    brain.has_solved.return_value = False
    brain.ping.return_value = True
    
    config = {"llm": {"model": "test"}, "max_retries_per_puzzle": 1}
    runner = DurableARCRunner(harness, brain, config)
    
    # Mocking orchestrator to return success=False but with coverage_saturated signal
    with patch("agents.arc3.runner.ARCOrchestrator") as MockOrch:
        orch = MockOrch.return_value
        orch.session_id = "test-session"
        orch._step_history = [{"step": 1, "reward": 0}]
        orch._consecutive_no_progress_steps = 5
        orch.cost_tracker = MagicMock()
        orch.cost_tracker.budget_exhausted = False
        orch._hypothesis_context = {"loop_detected": False}
        
        # KEY SIGNALS for A015
        orch._solve_context = {
            "graduation_reason": "coverage_saturated_high_confidence",
            "coverage_saturated": True
        }
        
        # Simulate run failure via exception to trigger mark_failed
        runner._run_puzzle = AsyncMock(side_effect=RuntimeError("Directional plan failed to reach goal"))
        
        with patch("agents.arc3.runner.CheckpointManager") as MockMgr:
            mgr = MockMgr.return_value
            mgr.load_or_create.return_value = MagicMock(tasks={})
            
            task = MockTask("task-1")
            results = await runner.run([task], "card-1")
            
            # Verify mark_failed was called with COVERAGE_SATURATED_ABORT
            mgr.mark_failed.assert_called_once()
            args = mgr.mark_failed.call_args[0]
            # args: (checkpoint, task_id, error_message, failure_class)
            assert args[3] == FailureTaxonomy.COVERAGE_SATURATED_ABORT.value


@pytest.mark.asyncio
async def test_failure_class_plateau_escalation_abort():
    """A018: plateau_escalation_required + coverage_saturated -> COVERAGE_SATURATED_ABORT."""
    harness = MagicMock()
    harness.mock_api = True
    harness.config.parameters = {"max_attempts_per_puzzle": 1}
    harness._get_mock_initial_frame.return_value = {"frame": [[0, 0], [0, 0]], "game_id": "test-game", "state": "NOT_STARTED"}
    harness.serializer = MagicMock()
    harness.serializer._estimate_tokens.return_value = 10
    
    brain = AsyncMock()
    brain.has_solved.return_value = False
    brain.ping.return_value = True
    
    config = {"llm": {"model": "test"}, "max_retries_per_puzzle": 1}
    runner = DurableARCRunner(harness, brain, config)
    
    with patch("agents.arc3.runner.ARCOrchestrator") as MockOrch:
        orch = MockOrch.return_value
        orch.session_id = "test-session"
        orch._step_history = [{"step": 1, "reward": 0}]
        orch._consecutive_no_progress_steps = 5
        orch.cost_tracker = MagicMock()
        orch.cost_tracker.budget_exhausted = False
        orch._hypothesis_context = {"loop_detected": True} # Usually a loop would be STUCK_IN_LOOP
        
        # KEY SIGNALS for A018
        orch._solve_context = {
            "plateau_escalation_required": True,
            "coverage_saturated": True
        }
        
        # Simulate run failure via exception to trigger mark_failed
        runner._run_puzzle = AsyncMock(side_effect=RuntimeError("Plateau memory exhausted"))
        
        with patch("agents.arc3.runner.CheckpointManager") as MockMgr:
            mgr = MockMgr.return_value
            mgr.load_or_create.return_value = MagicMock(tasks={})
            
            task = MockTask("task-1")
            results = await runner.run([task], "card-1")
            
            # Verify mark_failed was called with COVERAGE_SATURATED_ABORT
            # even though loop_detected was True, because escalation+saturation has higher priority
            mgr.mark_failed.assert_called_once()
            args = mgr.mark_failed.call_args[0]
            assert args[3] == FailureTaxonomy.COVERAGE_SATURATED_ABORT.value

