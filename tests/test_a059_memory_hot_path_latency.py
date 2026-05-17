
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from sidequest_mcp_client.mcp_brain_client import MCPBrainClient

@pytest.mark.asyncio
async def test_mcp_brain_client_caching_recall_procedures():
    """A059: Verify that recall_procedures is cached."""
    client = MCPBrainClient(cmd=["mock-cmd"])
    client._started = True
    client._initialized = True
    
    # Mock asyncio.to_thread (which calls session.call_tool)
    async def mock_thread_call(*args, **kwargs):
        return {"status": "ok", "procedures": []}
        
    with patch.object(asyncio, "to_thread", side_effect=mock_thread_call) as mock_exec:
        # 1. First call - should hit MCP
        r1 = await client.recall_procedures(archetype="race")
        assert r1["source"] == "fresh"
        assert mock_exec.call_count == 1
        
        # 2. Second call - should hit cache
        r2 = await client.recall_procedures(archetype="race")
        assert r2["source"] == "cache"
        assert r2["cache_hit"] is True
        assert mock_exec.call_count == 1

@pytest.mark.asyncio
async def test_mcp_brain_client_dedup_register_plan():
    """A059: Verify that register_plan is deduplicated."""
    client = MCPBrainClient(cmd=["mock-cmd"])
    client._started = True
    client._initialized = True
    
    async def mock_thread_call(*args, **kwargs):
        return {"status": "ok", "plan_id": "p1"}
        
    with patch.object(asyncio, "to_thread", side_effect=mock_thread_call) as mock_exec:
        # 1. First call - fresh
        r1 = await client.register_plan(goal="g1", steps=["a1"], session_id="s1")
        assert r1["source"] == "fresh"
        assert mock_exec.call_count == 1
        
        # 2. Second call with SAME arguments - dedup
        r2 = await client.register_plan(goal="g1", steps=["a1"], session_id="s1")
        assert r2["source"] == "dedup"
        assert r2["dedup_hit"] is True
        assert mock_exec.call_count == 1
        
        # 3. Third call with DIFFERENT arguments - fresh
        r3 = await client.register_plan(goal="g2", steps=["a1"], session_id="s1")
        assert r3["source"] == "fresh"
        assert mock_exec.call_count == 2

@pytest.mark.asyncio
async def test_mcp_brain_client_timeout_fallback_label():
    """A059: Verify that timeout returns fallback source metadata."""
    client = MCPBrainClient(cmd=["mock-cmd"])
    client._started = True
    client._initialized = True
    
    async def slow_call(*args, **kwargs):
        raise asyncio.TimeoutError("timeout")
        
    with patch.object(asyncio, "to_thread", side_effect=slow_call):
        resp = await client.current_truth(query="q", session_id="s", scope="g", limit=1)
        assert resp["status"] == "error"
        assert resp["source"] == "fallback"
        assert resp["fallback_reason"] == "timeout"
