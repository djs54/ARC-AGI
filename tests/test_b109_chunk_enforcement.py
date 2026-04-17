
import pytest
from unittest.mock import AsyncMock, MagicMock
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.solver import SolveEngine, PlanChunk, SolveContext
from benchmarks.arc3.state_serializer import StateSerializerForARC

@pytest.fixture
def orchestrator():
    brain = AsyncMock()
    # Mock recall_plans and recall_relevant_lessons to return data, not coroutines
    brain.recall_plans.return_value = {"plans": []}
    brain.recall_relevant_lessons.return_value = {"lessons": []}
    brain.analogical_search.return_value = {"results": []}
    brain.register_plan.return_value = {"plan_id": "test-plan"}
    brain.report_outcome.return_value = {"status": "ok"}
    
    llm = MagicMock()
    # Mock llm.achat to return a valid JSON string
    llm.achat = AsyncMock(return_value='{"condition_type":"reach_goal","description":"test","confidence":0.9}')
    
    serializer = StateSerializerForARC()
    return ARCOrchestrator(brain, llm, "session-1", serializer, {})

@pytest.fixture
def solver(orchestrator):
    return orchestrator.solve_engine

@pytest.mark.asyncio
async def test_enforce_action_policy_follows_chunk(orchestrator, solver):
    # Set up an active chunk in the solver
    chunk = PlanChunk(
        description="move left",
        estimated_actions=["ACTION3", "ACTION3"],
        plan_id="chunk-plan-1"
    )
    solver._active_chunk = chunk
    
    # Set up solve context in orchestrator (which caches it from solver)
    orchestrator._solve_context = {
        "active_chunk": {
            "description": chunk.description,
            "estimated_actions": list(chunk.estimated_actions),
            "plan_id": chunk.plan_id,
            "source": "bfs"
        }
    }
    
    available_actions = ["ACTION3", "ACTION4"]
    llm_action = {"action_id": "ACTION4", "rationale": "I want to go right"}
    
    # Enforce policy
    forced_action = orchestrator._enforce_action_policy(llm_action, available_actions)
    
    assert forced_action["action_id"] == "ACTION3"
    assert "enforcing" in forced_action["rationale"] and "chunk" in forced_action["rationale"]
    # Verify action was popped from solver's chunk
    assert solver._active_chunk.estimated_actions == ["ACTION3"]

@pytest.mark.asyncio
async def test_enforce_action_policy_pops_on_agreement(orchestrator, solver):
    chunk = PlanChunk(
        description="move left",
        estimated_actions=["ACTION3"],
        plan_id="chunk-plan-1",
        source="bfs"
    )
    solver._active_chunk = chunk
    orchestrator._solve_context = {
        "active_chunk": {
            "description": chunk.description,
            "estimated_actions": list(chunk.estimated_actions),
            "plan_id": chunk.plan_id,
            "source": "bfs"
        }
    }
    
    available_actions = ["ACTION3", "ACTION4"]
    llm_action = {"action_id": "ACTION3", "rationale": "I agree, left is good"}
    
    final_action = orchestrator._enforce_action_policy(llm_action, available_actions)
    
    assert final_action["action_id"] == "ACTION3"
    # Verify action was popped even when LLM agreed
    assert solver._active_chunk.estimated_actions == []

@pytest.mark.asyncio
async def test_solve_engine_registers_chunk_plan(solver):
    solver.brain.register_plan.return_value = {"plan_id": "new-chunk-id"}
    
    # Mock Chunker to return a new chunk
    new_chunk = PlanChunk(description="new strategy", estimated_actions=["ACTION1"])
    solver.plan_chunker.generate_chunk = MagicMock(return_value=new_chunk)
    
    # Need a victory condition to trigger chunking
    solver._victory_condition = MagicMock()
    solver._victory_condition.confidence = 0.9
    
    obs = {"available_actions": ["ACTION1"], "task_id": "t1", "dataset_id": "d1"}
    ctx = {"last_transition_effect": {"meaningful_change_score": 0.5}}
    
    await solver.solve(obs, ctx, step=1, state_graph=MagicMock(), current_state_hash="h1")
    
    # Verify SideQuests plan registration was called for the chunk
    solver.brain.register_plan.assert_called()
    assert solver._active_chunk.plan_id == "new-chunk-id"

@pytest.mark.asyncio
async def test_dissonance_triggers_outcome_report(solver):
    # Set up an active chunk with a plan_id
    chunk = PlanChunk(description="stalled chunk", plan_id="plan-to-fail")
    solver._active_chunk = chunk
    
    # Mock DissonanceDetector to trigger replan
    solver.dissonance_detector.update = MagicMock(return_value=(True, "stalled test"))
    
    obs = {"available_actions": ["ACTION1"], "task_id": "t1", "dataset_id": "d1"}
    ctx = {"last_transition_effect": {"meaningful_change_score": 0.0}}
    
    await solver.solve(obs, ctx, step=10, state_graph=MagicMock(), current_state_hash="h1")
    
    # Verify report_outcome was called with negative valence for the OLD plan_id
    solver.brain.report_outcome.assert_called_with(
        plan_id="plan-to-fail",
        outcome="Chunk stalled: stalled test",
        valence=-0.6,
        session_id=solver.session_id,
        valence_source="dissonance_detector"
    )
    # Verify a NEW chunk was started (it won't be None because solve() generates a new one)
    assert solver._active_chunk is not None
    assert solver._active_chunk.description != "stalled chunk"
