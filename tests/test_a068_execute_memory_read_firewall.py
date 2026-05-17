import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from sidequest_mcp_client.mcp_brain_client import MCPBrainClient
from benchmarks.arc3.adapter import LedgerBrainClient

@pytest.mark.asyncio
async def test_mcp_brain_client_firewall_skip():
    # Setup MCP client with firewall enabled
    client = MCPBrainClient(cmd=["true"])
    client._started = True
    client._initialized = True
    client.current_phase = "execute"
    client._firewall_enabled = True
    
    # Mock session
    client._session = MagicMock()
    client._session.call_tool = MagicMock()
    
    # Case 1: Cache miss in execute phase should return skipped
    resp = await client.call_tool("recall_lessons", {"lesson_type": "test"})
    assert resp["status"] == "skipped"
    assert resp["memory_firewall_action"] == "skipped"
    assert client._session.call_tool.called is False
    assert resp["memory_degraded"] is False

@pytest.mark.asyncio
async def test_ledger_brain_client_firewall_compliance():
    inner = AsyncMock()
    inner.recall_lessons.return_value = {
        "status": "skipped",
        "memory_firewall_action": "skipped",
        "lessons": []
    }
    
    ledger = []
    step_provider = lambda: 1
    client = LedgerBrainClient(inner, ledger, step_provider)
    client.current_phase = "execute"
    
    # Call recall_lessons
    resp = await client.recall_lessons(lesson_type="test")
    
    assert resp["status"] == "skipped"
    # Check ledger entry mode
    assert ledger[-1]["mode"] == "read:skipped"
    assert ledger[-1]["call_type"] == "recall_lessons"

@pytest.mark.asyncio
async def test_analogical_search_vector_tolerance():
    inner = AsyncMock()
    ledger = []
    client = LedgerBrainClient(inner, ledger, lambda: 1)
    
    # Should tolerate vector-only search
    await client.analogical_search(vector={"a": 1}, current_quest_id="q1", limit=5, min_similarity=0.5)
    inner.analogical_search.assert_called_once()
    args = inner.analogical_search.call_args[1]
    assert args["vector"] == {"a": 1}
    assert args["query"] == "" or args["query"] is None
