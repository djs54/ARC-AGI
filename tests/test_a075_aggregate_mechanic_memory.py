import pytest
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.world_model import WorldModelGraph

@pytest.fixture
def orchestrator():
    brain = MagicMock()
    brain.publish_mechanic_summary = AsyncMock()
    brain.recall_mechanic_priors = AsyncMock(return_value={"results": [{"id": "m1"}]})
    llm = MagicMock()
    serializer = MagicMock()
    config = {"task_id": "t1"}
    orch = ARCOrchestrator(brain, llm, "s1", serializer, config)
    return orch

@pytest.mark.asyncio
async def test_publish_mechanic_memory(orchestrator):
    # Setup graph with high confidence
    orchestrator.world_model.upsert_hypothesis("h1", "rule", "test", 0.8, "active")
    
    await orchestrator.publish_mechanic_memory()
    
    orchestrator.brain.publish_mechanic_summary.assert_called_once()
    summary = orchestrator.brain.publish_mechanic_summary.call_args.kwargs["summary"]
    assert summary["task_id"] == "t1"
    assert summary["confidence"] == 0.8

@pytest.mark.asyncio
async def test_publish_mechanic_memory_includes_all_actions_churn_failure(orchestrator):
    step = 0
    for action_id in ["ACTION5", "ACTION6", "ACTION7"]:
        for _ in range(2):
            step += 1
            state_id = orchestrator.world_model.record_state(step=step, frame_hash=f"hash-{step}")
            action_node = orchestrator.world_model.record_action(
                step=step,
                action_id=action_id,
                args={},
                state_id=state_id,
            )
            obs_id = orchestrator.world_model.add_node(
                f"obs-churn-{step}",
                "Observation",
                {"step": step, "hash": f"obs-{step}"},
            )
            orchestrator.world_model.record_effect(
                action_node,
                obs_id,
                "pixel_churn",
                {"step": step, "meaningful": False},
            )

    await orchestrator.publish_mechanic_memory()

    orchestrator.brain.publish_mechanic_summary.assert_called_once()
    summary = orchestrator.brain.publish_mechanic_summary.call_args.kwargs["summary"]
    assert "all_actions_churn_no_progress" in summary["failure_modes"]
    assert summary["all_actions_churn_evidence"]["all_actions_churn"] is True

@pytest.mark.asyncio
async def test_retrieve_mechanic_priors(orchestrator):
    priors = await orchestrator.retrieve_mechanic_priors()
    
    orchestrator.brain.recall_mechanic_priors.assert_called_once()
    assert "signature" in orchestrator.brain.recall_mechanic_priors.call_args.kwargs
    assert len(priors) == 1
    assert priors[0]["id"] == "m1"
