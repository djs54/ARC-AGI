import pytest
from unittest.mock import MagicMock

from benchmarks.arc3.world_model_eval import WorldModelEvaluator
from sidequest_mcp_client.mcp_brain_client import MCPBrainClient


@pytest.mark.asyncio
async def test_mcp_mechanic_prior_normalization_exposes_diagnostic_fields():
    client = MCPBrainClient(cmd=["true"])
    client._started = True
    client._initialized = True
    client._session = MagicMock()
    client._session.call_tool = MagicMock(return_value={"results": [{"id": "m1"}]})

    resp = await client.recall_mechanic_priors(signature={"action_set": "A6"})

    assert resp["mechanic_prior_recall_status"] == "ok"
    assert resp["mechanic_prior_count"] == 1
    assert resp["mechanic_prior_error_code"] is None


@pytest.mark.parametrize(
    ("snapshot", "expected_state", "expected_active"),
    [
        ({"mechanic_prior_recall_status": "capability_missing", "mechanic_prior_count": 0, "mechanic_priors_used_count": 0}, "capability_missing", False),
        ({"mechanic_prior_recall_status": "ok", "mechanic_prior_count": 0, "mechanic_priors_used_count": 0}, "zero_priors", False),
        ({"mechanic_prior_recall_status": "ok", "mechanic_prior_count": 2, "mechanic_priors_used_count": 0}, "priors_recalled_not_used", False),
        ({"mechanic_prior_recall_status": "ok", "mechanic_prior_count": 2, "mechanic_priors_used_count": 1}, "prior_used", True),
    ],
)
def test_world_model_summary_distinguishes_mechanic_transfer_states(snapshot, expected_state, expected_active):
    evaluator = WorldModelEvaluator()
    evaluator.build_step_row("task-1", 1, snapshot)

    summary = evaluator.build_summary_row("task-1", {"world_model_snapshot": {"node_count": 3}})

    assert summary.memory_transfer_state == expected_state
    assert summary.memory_transfer_active is expected_active
