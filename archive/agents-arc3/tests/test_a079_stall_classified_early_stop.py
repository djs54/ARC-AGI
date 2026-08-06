import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.orchestrator import ARCOrchestrator, ReasoningMode
from agents.arc3.reasoning_controller import ReasoningController
from agents.arc3.solver import SolveContext, GameArchetype, VictoryCondition, VictoryType

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
    config = {
        "task_id": "test_task",
        "reasoning_gate": {
            "min_probes_before_stop": 2,
            "stall_threshold": 3
        }
    }
    orch = ARCOrchestrator(brain, llm, "session1", serializer, config)
    
    # Mock solve engine with a valid SolveContext return value
    orch.solve_engine = MagicMock()
    mock_ctx = SolveContext()
    mock_ctx.archetype = GameArchetype.UNKNOWN
    mock_ctx.archetype_confidence = 0.5
    mock_ctx.object_roles = {}
    mock_ctx.victory_condition = VictoryCondition(condition_type=VictoryType.UNKNOWN, description="test", target_color_id=None, confidence=0.0)
    mock_ctx.active_chunk = None
    mock_ctx.dissonance_detected = False
    mock_ctx.dissonance_reason = None
    mock_ctx.strategy_summary = "test"
    mock_ctx.chunk_ledger = []
    mock_ctx.plateau_mode = False
    mock_ctx.plateau_reason = ""
    mock_ctx.ranked_action_families = []
    mock_ctx.action_family_scores = {}
    
    orch.solve_engine.solve = AsyncMock(return_value=mock_ctx)
    return orch


def test_route_regression_early_stop_suppressed_for_graph_configuration_goal():
    controller = ReasoningController({"reasoning_gate": {"route_regression_threshold": 1}})

    class MockClaim:
        kind = "action_effect"
        effect_class = "distance_regressing_move"
        props = {"distance_trend": "regressing"}

    class MockDelta:
        claims = [MockClaim()]
        failure_signal = None
        step = 10

    decision = controller.decide(
        world_summary="",
        compiled_delta=MockDelta(),
        budget_state={
            "active_goal_type": "color_correspondence",
            "active_goal_confidence": 0.8,
            "route_transition_evidence": {
                "has_route_evidence": True,
                "has_recent_route_regression": True,
            },
        },
        phase="solve",
        active_hypotheses=[],
        available_actions=["ACTION1", "ACTION2"],
        per_action_evidence={
            "ACTION1": {"tested_count": 1, "recent_effects": ["pixel_churn"]},
            "ACTION2": {"tested_count": 1, "recent_effects": ["pixel_churn"]},
        },
    )

    assert decision.mode == ReasoningMode.LLM_REASON
    assert decision.trigger == "route_regression_suppressed_for_graph_goal"
    assert decision.early_stop_suppressed_reason == "color_correspondence_active"

@pytest.mark.asyncio
async def test_stall_classified_early_stop(orchestrator):
    obs = {"grid": [[1]], "available_actions": ["ACTION1"]}
    
    # 1. First stall
    class MockDelta1:
        claims = [type('Claim', (), {'kind': 'action_effect', 'effect_class': 'no_op'})()]
        failure_signal = "single_action_terminal_stall"
        step = 1
    orchestrator._compiled_delta = MockDelta1()
    
    await orchestrator.solve(obs, {}, 1)
    assert orchestrator._last_reasoning_decision.mode == ReasoningMode.CHEAP_PROBE
    assert orchestrator._force_replan is False
    
    # 2. Second stall
    orchestrator._compiled_delta = MockDelta1()
    await orchestrator.solve(obs, {}, 2)
    assert orchestrator._last_reasoning_decision.mode == ReasoningMode.CHEAP_PROBE
    
    # 3. Third stall (hitting threshold=3)
    # But wait, min_probes_before_stop=2. 
    # Skip counts so far: 1, 2. So total_probes=2.
    orchestrator._compiled_delta = MockDelta1()
    await orchestrator.solve(obs, {}, 3)
    assert orchestrator._last_reasoning_decision.mode == ReasoningMode.EARLY_STOP
    assert orchestrator._force_replan is True

@pytest.mark.asyncio
async def test_stall_reset_on_progress(orchestrator):
    obs = {"grid": [[1]], "available_actions": ["ACTION1"]}
    
    # 1. Stall
    class MockDeltaStall:
        claims = [type('Claim', (), {'kind': 'action_effect', 'effect_class': 'no_op'})()]
        failure_signal = "single_action_terminal_stall"
        step = 1
    orchestrator._compiled_delta = MockDeltaStall()
    await orchestrator.solve(obs, {}, 1)
    assert orchestrator.reasoning_controller._consecutive_stalls == 1
    
    # 2. Progress
    class MockDeltaProgress:
        claims = [type('Claim', (), {'kind': 'action_effect', 'effect_class': 'object_progress'})()]
        failure_signal = None
        step = 2
    orchestrator._compiled_delta = MockDeltaProgress()
    await orchestrator.solve(obs, {}, 2)
    assert orchestrator.reasoning_controller._consecutive_stalls == 0

@pytest.mark.asyncio
async def test_delayed_reward_guardrail(orchestrator):
    obs = {"grid": [[1]], "available_actions": ["ACTION1"]}
    
    # Setup mechanic prior with delayed reward prediction
    orchestrator.brain.recall_mechanic_priors = AsyncMock(return_value={
        "results": [{"id": "m1", "predicts_delayed_reward": True, "effect_observed": False}]
    })
    
    # Stall past threshold
    class MockDeltaStall:
        claims = [type('Claim', (), {'kind': 'action_effect', 'effect_class': 'no_op'})()]
        failure_signal = "single_action_terminal_stall"
        step = 1
        
    orchestrator._compiled_delta = MockDeltaStall()
    
    # Run through stalls
    for i in range(5):
        await orchestrator.solve(obs, {}, i+1)
        
    # Should stay in CHEAP_PROBE because of delayed reward guardrail
    assert orchestrator._last_reasoning_decision.mode == ReasoningMode.CHEAP_PROBE
    assert orchestrator._last_reasoning_decision.stall_policy == "delayed_reward_wait"
    assert orchestrator._force_replan is False


@pytest.mark.asyncio
async def test_single_action_pixel_churn_gets_larger_tick_probe_budget(orchestrator):
    obs = {"grid": [[1]], "available_actions": ["ACTION1"]}
    orchestrator.reasoning_controller._max_single_action_tick_probes = 6

    class MockDeltaTick:
        claims = [type('Claim', (), {'kind': 'action_effect', 'effect_class': 'pixel_churn'})()]
        failure_signal = "single_action_terminal_stall"
        step = 1

    for i in range(5):
        MockDeltaTick.step = i + 1
        orchestrator._compiled_delta = MockDeltaTick()
        await orchestrator.solve(obs, {}, i + 1)
        assert orchestrator._last_reasoning_decision.mode == ReasoningMode.CHEAP_PROBE
        assert orchestrator._last_reasoning_decision.stall_policy == "tick_probe"
        assert orchestrator._force_replan is False


@pytest.mark.asyncio
async def test_cheap_probe_refreshes_planner_with_single_action_prior(orchestrator):
    obs = {"grid": [[1]], "available_actions": ["ACTION6"]}
    orchestrator.brain.recall_mechanic_priors = AsyncMock(return_value={
        "results": [
            {
                "id": "mechanic:single-transfer",
                "confidence": 0.8,
                "effects": [{"action": "ACTION2", "effect_class": "delayed_reward", "confidence": 0.7}],
            }
        ],
        "mechanic_prior_count": 1,
    })

    class MockDeltaTick:
        claims = [type('Claim', (), {'kind': 'action_effect', 'effect_class': 'pixel_churn'})()]
        failure_signal = "single_action_terminal_stall"
        step = 1

    orchestrator._compiled_delta = MockDeltaTick()

    await orchestrator.solve(obs, {}, 1)

    selection = orchestrator._last_planner_selection
    assert orchestrator._last_reasoning_decision.mode == ReasoningMode.CHEAP_PROBE
    assert selection.selected.action_id == "ACTION6"
    assert selection.selected.mechanic_prior_id == "mechanic:single-transfer"
    assert selection.selected.predicted_observation["effect_class"] == "delayed_reward"
    assert selection.mechanic_priors_used == 1
