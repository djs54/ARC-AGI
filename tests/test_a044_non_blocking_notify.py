
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from sidequest_mcp_client.mcp_brain_client import MCPBrainClient

@pytest.mark.asyncio
async def test_notify_turn_async_dispatch():
    """A044: Verify that notify_turn returns immediately when async_dispatch=True."""
    client = MCPBrainClient(cmd=["mock-cmd"])
    client._started = True
    client._initialized = True
    
    # Mock call_tool to simulate latency
    async def slow_call(*args, **kwargs):
        await asyncio.sleep(0.1)
        return {"status": "ok"}
    
    with patch.object(client, "call_tool", side_effect=slow_call) as mock_call:
        start_t = asyncio.get_event_loop().time()
        
        # Call with explicit async_dispatch (A051: now default and strict)
        resp = await client.notify_turn(
            role="user", content="hello", session_id="s1", async_dispatch=True
        )
        
        end_t = asyncio.get_event_loop().time()
        
        assert resp["status"] == "queued_async"
        assert resp["mode"] == "async_background"
        # Should return almost immediately (< 0.05s) despite slow_call latency
        assert end_t - start_t < 0.05
        
        # A051: Verify it is strict even if False is passed
        resp2 = await client.notify_turn(
            role="user", content="hello2", session_id="s1", async_dispatch=False
        )
        assert resp2["status"] == "queued_async"
        assert resp2["mode"] == "async_background"
        
        # Wait for worker to process
        await asyncio.sleep(0.3)
        assert mock_call.call_count == 2
        
    await client.close()

@pytest.mark.asyncio
async def test_notify_turn_queue_limit():
    """A044: Verify that notify_turn drops events when the queue is full."""
    client = MCPBrainClient(cmd=["mock-cmd"])
    client._started = True
    client._initialized = True
    client._notify_queue_limit = 2 # Very small queue for testing
    
    # Mock call_tool to be very slow so queue fills up
    async def slow_call(*args, **kwargs):
        await asyncio.sleep(0.5)
        return {"status": "ok"}
        
    with patch.object(client, "call_tool", side_effect=slow_call):
        # Fill the queue
        await client.notify_turn(role="u", content="c1", session_id="s", async_dispatch=True)
        await client.notify_turn(role="u", content="c2", session_id="s", async_dispatch=True)
        
        # This one should be dropped
        resp = await client.notify_turn(role="u", content="c3", session_id="s", async_dispatch=True)
        
        assert resp["status"] == "dropped_full"
        assert resp["mode"] == "async_background"
        assert client._notify_dropped_count == 1
        
    await client.close()
