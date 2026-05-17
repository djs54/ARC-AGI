import pytest
from unittest.mock import MagicMock, AsyncMock
from sidequest_mcp_client.mcp_brain_client import MCPBrainClient
from agents.arc3.world_model_planner import WorldModelPlanner, PlanMode

@pytest.mark.asyncio
async def test_mcp_brain_client_mechanic_normalization():
    client = MCPBrainClient(cmd=["true"])
    client._started = True
    client._initialized = True
    
    # Mock session for recall_mechanic_priors
    client._session = MagicMock()
    client._session.call_tool = MagicMock(return_value={
        "results": [{"id": "m1"}]
    })
    
    resp = await client.recall_mechanic_priors(signature={"a": 1})
    assert resp["status"] == "ok"
    assert resp["prior_count"] == 1
    assert resp["results"][0]["id"] == "m1"
    
    # Mock session for publish_mechanic_summary
    client._session.call_tool = MagicMock(return_value={})
    resp = await client.publish_mechanic_summary(summary={"id": "m1"})
    assert resp["status"] == "ok"
    assert resp["write_ok"] is True

def test_planner_prior_provenance():
    planner = WorldModelPlanner()
    
    class MockWorldModel:
        def get_active_hypotheses(self): return []
        
    mechanic_priors = [
        {
            "id": "mech:test",
            "confidence": 0.9,
            "effects": [{"action": "ACTION6"}]
        }
    ]
    
    selection = planner.select_next_candidate(
        world_model=MockWorldModel(),
        mechanic_priors=mechanic_priors,
        available_actions=["ACTION6"],
        budget_state={}
    )
    
    # The prior should generate an EXPLOIT candidate that wins
    assert selection.selected.action_id == "ACTION6"
    assert selection.selected.mode == PlanMode.EXPLOIT
    assert selection.selected.mechanic_prior_id == "mech:test"
    assert selection.selected.mechanic_prior_source == "aggregate"
    assert selection.mechanic_priors_used == 1

@pytest.mark.asyncio
async def test_mcp_brain_client_capability_missing():
    client = MCPBrainClient(cmd=["true"])
    client._started = True
    client._initialized = True
    
    # Mock session to raise MCPToolNotFound
    from sidequest_mcp_client.mcp_session import MCPToolNotFound
    client._session = MagicMock()
    client._session.call_tool = MagicMock(side_effect=MCPToolNotFound("Tool not found"))
    
    resp = await client.recall_mechanic_priors(signature={})
    assert resp["status"] == "capability_missing"
    assert resp["results"] == []
