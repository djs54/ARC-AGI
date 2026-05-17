import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.orchestrator import ARCOrchestrator, ReasoningMode
from agents.arc3.solver import SolveContext, GameArchetype

@pytest.fixture
def orchestrator():
    brain = MagicMock()
    brain.recall_relevant_lessons = AsyncMock(return_value={"lessons": []})
    brain.current_truth = AsyncMock(return_value={"results": []})
    brain.recall_procedures = AsyncMock(return_value={"procedures": []})
    brain.recall_mechanic_priors = AsyncMock(return_value={"results": []})
    
    llm = MagicMock()
    serializer = MagicMock()
    serializer._estimate_tokens = MagicMock(return_value=100)
    config = {"task_id": "test_task"}
    orch = ARCOrchestrator(brain, llm, "session1", serializer, config)
    
    # Mock solve engine with a valid SolveContext return value
    orch.solve_engine = MagicMock()
    mock_ctx = SolveContext()
    mock_ctx.archetype = GameArchetype.UNKNOWN
    mock_ctx.object_roles = {}
    orch.solve_engine.solve = AsyncMock(return_value=mock_ctx)
    
    return orch

@pytest.mark.asyncio
async def test_a076_reasoning_gating_integration(orchestrator):
    # Set low threshold for testing
    orchestrator.reasoning_controller._stall_threshold = 1
    orchestrator.reasoning_controller._min_probes_before_stop = 0
    
    # 1. Setup a single action stall scenario
    obs = {"grid": [[1]], "available_actions": ["ACTION1"]}
    
    # Mock the compiler to emit a stall signal
    class MockDelta:
        claims = [type('Claim', (), {'kind': 'action_effect', 'effect_class': 'no_op'})()]
        failure_signal = "single_action_terminal_stall"
        step = 1

    orchestrator._compiled_delta = MockDelta()
    
    # 2. Call solve
    # The reasoning controller should decide EARLY_STOP (stalls=1 >= threshold=1)
    result = await orchestrator.solve(obs, {}, 1)
    
    # 3. Verify solve_engine.solve was NOT called (gated)
    assert orchestrator.solve_engine.solve.called is False
    assert orchestrator._force_replan is True
    
    # 4. Verify trace event was emitted
    trace_events = [e for e in orchestrator._execution_trace if e.get("operation") == "reasoning_gating"]
    assert len(trace_events) > 0
    assert trace_events[0]["details"]["mode"] == "early_stop"

@pytest.mark.asyncio
async def test_a076_cheap_execute_logic(orchestrator):
    # 1. Setup a single action stall (not terminal yet)
    obs = {"grid": [[1]], "available_actions": ["ACTION1"]}
    
    # Mock the compiler for a no_op claim but NO failure_signal yet
    class MockDelta:
        claims = [type('Claim', (), {'kind': 'action_effect', 'effect_class': 'no_op'})()]
        failure_signal = None
        step = 1

    orchestrator._compiled_delta = MockDelta()
    
    # Refined Rule 3 in A079 requires stalls > 1
    # First call: stalls=1
    await orchestrator.solve(obs, {}, 1)
    assert orchestrator.reasoning_controller._consecutive_stalls == 1
    
    # Second call: stalls=2
    await orchestrator.solve(obs, {}, 2)
    assert orchestrator.reasoning_controller._consecutive_stalls == 2
    
    # Check trace for cheap_probe (replaces cheap_execute in A079)
    trace_events = [e for e in orchestrator._execution_trace if e.get("operation") == "reasoning_gating"]
    # We look at the second event (from step 2)
    assert trace_events[1]["details"]["mode"] == "cheap_probe"
    assert orchestrator.reasoning_controller.skip_count == 1

