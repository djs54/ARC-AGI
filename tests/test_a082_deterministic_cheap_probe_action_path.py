import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.reasoning_controller import ReasoningDecision, ReasoningMode


def _observation(actions):
    return {
        "grid": [[0]],
        "colors": [{"value": 0, "count": 1}],
        "shapes": [],
        "frame_hash": "hash-1",
        "available_actions": list(actions),
        "state": "NOT_FINISHED",
    }


@pytest.mark.asyncio
async def test_cheap_probe_uses_deterministic_fallback_without_llm():
    brain = AsyncMock()
    brain.notify_turn.return_value = {"status": "ok"}
    serializer = MagicMock()
    serializer._estimate_tokens.return_value = 100
    orchestrator = ARCOrchestrator(brain, MagicMock(), "session-1", serializer, {})
    orchestrator._mental_sandbox = AsyncMock(return_value={"action_id": "ACTION1"})
    orchestrator._last_reasoning_decision = ReasoningDecision(
        mode=ReasoningMode.CHEAP_PROBE,
        trigger="single_action_stall_mitigation",
    )

    action = await orchestrator.act(_observation(["ACTION6"]), {}, step_num=1)

    assert action["action_id"] == "ACTION6"
    assert action["decision_source"] == "cheap_probe"
    assert action["cheap_probe_reason"] == "deterministic_fallback"
    assert action["bypassed_llm"] is True
    orchestrator._mental_sandbox.assert_not_called()
    assert orchestrator._step_history[-1]["decision_flow"]["decision_source"] == "cheap_probe"
    assert orchestrator._step_history[-1]["cheap_probe_reason"] == "deterministic_fallback"
    assert any(
        event.get("operation") == "cheap_probe_applied"
        and event.get("details", {}).get("bypassed_llm") is True
        for event in orchestrator._execution_trace
    )


@pytest.mark.asyncio
async def test_cheap_probe_uses_last_planner_selection_and_preserves_provenance():
    brain = AsyncMock()
    brain.notify_turn.return_value = {"status": "ok"}
    serializer = MagicMock()
    serializer._estimate_tokens.return_value = 100
    orchestrator = ARCOrchestrator(brain, MagicMock(), "session-2", serializer, {})
    orchestrator._mental_sandbox = AsyncMock(return_value={"action_id": "ACTION6"})
    orchestrator._last_reasoning_decision = ReasoningDecision(
        mode=ReasoningMode.CHEAP_PROBE,
        trigger="delayed_reward_probe",
    )
    orchestrator._last_planner_selection = SimpleNamespace(
        candidate_count=2,
        selected=SimpleNamespace(
            action_id="ACTION1",
            args={"x": 2, "y": 3},
            mechanic_prior_id="prior-7",
            mechanic_prior_source="aggregate",
            evidence_path="mechanic_prior_prior-7",
        ),
    )

    action = await orchestrator.act(_observation(["ACTION1", "ACTION6"]), {}, step_num=1)

    assert action["action_id"] == "ACTION1"
    assert action["x"] == 2
    assert action["y"] == 3
    assert action["planner_selected_prior_id"] == "prior-7"
    assert action["planner_selected_prior_source"] == "aggregate"
    assert action["evidence_path"] == "mechanic_prior_prior-7"
    assert action["cheap_probe_reason"] == "planner_selection"
    orchestrator._mental_sandbox.assert_not_called()


@pytest.mark.asyncio
async def test_cheap_probe_prefers_refreshed_planner_over_stale_solve_context():
    brain = AsyncMock()
    brain.notify_turn.return_value = {"status": "ok"}
    serializer = MagicMock()
    serializer._estimate_tokens.return_value = 100
    orchestrator = ARCOrchestrator(brain, MagicMock(), "session-stale", serializer, {})
    orchestrator._mental_sandbox = AsyncMock(return_value={"action_id": "ACTION4"})
    orchestrator._last_reasoning_decision = ReasoningDecision(
        mode=ReasoningMode.CHEAP_PROBE,
        trigger="prediction_contradiction_pressure",
    )
    orchestrator._solve_context = {
        "planner_selection": SimpleNamespace(
            candidate_count=2,
            selected=SimpleNamespace(action_id="ACTION4", args={}, evidence_path="stale"),
        )
    }
    orchestrator._last_planner_selection = SimpleNamespace(
        candidate_count=3,
        selected=SimpleNamespace(action_id="ACTION1", args={"x": 1, "y": 0}, evidence_path="fresh"),
    )

    action = await orchestrator.act(_observation(["ACTION1", "ACTION4"]), {}, step_num=1)

    assert action["action_id"] == "ACTION1"
    assert action["evidence_path"] == "fresh"
    orchestrator._mental_sandbox.assert_not_called()


@pytest.mark.asyncio
async def test_multi_action_churn_probe_uses_deterministic_path_and_avoids_harmful():
    brain = AsyncMock()
    brain.notify_turn.return_value = {"status": "ok"}
    serializer = MagicMock()
    serializer._estimate_tokens.return_value = 100
    orchestrator = ARCOrchestrator(brain, MagicMock(), "session-3", serializer, {})
    orchestrator._mental_sandbox = AsyncMock(return_value={"action_id": "ACTION2"})
    orchestrator._last_reasoning_decision = ReasoningDecision(
        mode=ReasoningMode.MULTI_ACTION_CHURN_PROBE,
        trigger="multi_action_churn",
    )
    orchestrator._step_history = [
        {"step": 1, "action_id": "ACTION1", "compiled_world_delta": {"effect_class": "harmful"}},
        {"step": 2, "action_id": "ACTION2", "compiled_world_delta": {"effect_class": "pixel_churn"}},
    ]

    action = await orchestrator.act(_observation(["ACTION1", "ACTION2", "ACTION3"]), {}, step_num=3)

    assert action["decision_source"] == "cheap_probe"
    assert action["cheap_probe_reason"] == "multi_action_churn"
    assert action["action_id"] == "ACTION3"
    orchestrator._mental_sandbox.assert_not_called()
    assert orchestrator._step_history[-1]["decision_flow"]["decision_source"] == "cheap_probe"
    assert any(
        event.get("operation") == "cheap_probe_applied"
        and event.get("details", {}).get("trigger") == "multi_action_churn"
        for event in orchestrator._execution_trace
    )


@pytest.mark.asyncio
async def test_multi_action_churn_probe_exploits_graph_productive_path():
    brain = AsyncMock()
    brain.notify_turn.return_value = {"status": "ok"}
    serializer = MagicMock()
    serializer._estimate_tokens.return_value = 100
    orchestrator = ARCOrchestrator(brain, MagicMock(), "session-graph-exploit", serializer, {})
    orchestrator._mental_sandbox = AsyncMock(return_value={"action_id": "ACTION6"})
    orchestrator._last_reasoning_decision = ReasoningDecision(
        mode=ReasoningMode.MULTI_ACTION_CHURN_PROBE,
        trigger="multi_action_churn",
    )

    state_id = orchestrator.world_model.record_state(step=1, frame_hash="hash1")
    action_node = orchestrator.world_model.record_action(step=1, action_id="ACTION2", args={}, state_id=state_id)
    obs_id = orchestrator.world_model.add_node("obs-progress", "Observation", {"step": 1, "hash": "obs"})
    orchestrator.world_model.record_effect(action_node, obs_id, "object_progress", {"step": 1, "meaningful": True})
    orchestrator._step_history = [
        {"step": 1, "action_id": "ACTION6", "compiled_world_delta": {"effect_class": "pixel_churn"}},
    ]

    action = await orchestrator.act(_observation(["ACTION1", "ACTION2", "ACTION6"]), {}, step_num=2)

    assert action["action_id"] == "ACTION2"
    assert action["decision_source"] == "cheap_probe"
    assert action["cheap_probe_reason"] == "productive_graph_path"
    assert action["evidence_path"].startswith("productive_path:ACTION2")
    orchestrator._mental_sandbox.assert_not_called()
