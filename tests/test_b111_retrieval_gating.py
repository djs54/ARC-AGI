
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from sidequest_mcp_client.mcp_brain_client import MCPBrainClient

@pytest.mark.asyncio
async def test_mcp_brain_client_caching():
    mock_session = MagicMock()
    # Mock call_tool to return a simple successful response
    mock_session.call_tool.return_value = {"status": "ok", "results": ["fact1"]}
    
    client = MCPBrainClient(session=mock_session)
    client._started = True
    client._initialized = True
    
    # First call: cache miss
    res1 = await client.current_truth(query="test", session_id="s1", scope="branch", limit=5)
    assert res1["results"] == ["fact1"]
    assert mock_session.call_tool.call_count == 1
    
    # Second call: cache hit
    res2 = await client.current_truth(query="test", session_id="s1", scope="branch", limit=5)
    assert res2["results"] == ["fact1"]
    # Should still be 1 because it hit the cache
    assert mock_session.call_tool.call_count == 1
    
    # Different query: cache miss
    res3 = await client.current_truth(query="different", session_id="s1", scope="branch", limit=5)
    assert mock_session.call_tool.call_count == 2
    
    # Manual clear
    client.clear_cache()
    await client.current_truth(query="test", session_id="s1", scope="branch", limit=5)
    assert mock_session.call_tool.call_count == 3

@pytest.mark.asyncio
async def test_mcp_brain_client_queued_offline_logging(caplog):
    import logging
    mock_session = MagicMock()
    mock_session.call_tool.return_value = {"status": "queued_offline", "payload": {"reason": "transport closed"}}
    
    client = MCPBrainClient(session=mock_session)
    client._started = True
    client._initialized = True
    
    with caplog.at_level(logging.WARNING):
        await client.notify_turn(role="user", content="test", session_id="s1")
        assert "returned 'queued_offline'" in caplog.text
        assert "transport closed" in caplog.text
