
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from sidequest_mcp_client.mcp_brain_client import MCPBrainClient
from agents.arc3.failure_taxonomy import classify_failure, FailureTaxonomy

@pytest.mark.asyncio
async def test_memory_firewall_skips_read_in_execute():
    """A064: Verify that memory reads are skipped in execute phase."""
    client = MCPBrainClient()
    client._started = True
    client._initialized = True
    client._session = MagicMock()
    
    client.current_phase = "execute"
    
    # current_truth is a cached method
    res = await client.current_truth(query="test", session_id="s", scope="s", limit=1)
    
    assert res["status"] == "skipped"
    assert res["memory_firewall_action"] == "skipped"
    assert "execute_memory_firewall" or "read_blocked" in str(res)

@pytest.mark.asyncio
async def test_memory_firewall_uses_cache_in_execute():
    """A064: Verify that memory reads use cache even in execute phase."""
    client = MCPBrainClient()
    client._started = True
    client._initialized = True
    client._session = MagicMock()
    
    # Seed cache
    import time
    client._cache[("current_truth", '{"limit": 1, "query": "test", "scope": "s", "session_id": "s"}')] = (time.time(), {"status": "ok", "data": "cached_val"})
    
    client.current_phase = "execute"
    
    res = await client.current_truth(query="test", session_id="s", scope="s", limit=1)
    
    assert res["source"] == "cache"
    assert res["cache_hit"] is True
    assert res["memory_firewall_action"] == "cached"

@pytest.mark.asyncio
async def test_memory_firewall_defers_write_in_macro():
    """A064: Verify that memory writes are deferred in macro phase."""
    client = MCPBrainClient()
    client._started = True
    client._initialized = True
    client._session = MagicMock()
    
    client.current_phase = "macro"
    
    res = await client.upsert_lesson(domain="d", text="t", valence=1.0)
    
    assert res["status"] == "deferred"
    assert res["memory_firewall_action"] == "deferred"
    assert len(client._deferred_writes) == 1

@pytest.mark.asyncio
async def test_memory_firewall_flush():
    """A064: Verify that deferred writes are flushed correctly."""
    client = MCPBrainClient()
    client._started = True
    client._initialized = True
    client._session = MagicMock()
    client._session.call_tool.return_value = {"status": "ok"}
    
    client.current_phase = "macro"
    await client.upsert_lesson(domain="d", text="t", valence=1.0)
    assert len(client._deferred_writes) == 1
    
    flush_res = await client.flush_deferred_writes()
    assert flush_res["status"] == "ok"
    assert flush_res["count"] == 1
    assert len(client._deferred_writes) == 0
    client._session.call_tool.assert_called_once()

def test_failure_taxonomy_daemon_timeout():
    """A064: Verify that daemon timeouts are classified as tool timeouts."""
    res = classify_failure(error_message="daemon_timeout: MCP connection lost")
    assert res == FailureTaxonomy.TOOL_TIMEOUT
    
    res = classify_failure(error_message="memory_timeout during recall")
    assert res == FailureTaxonomy.TOOL_TIMEOUT

@pytest.mark.asyncio
async def test_orchestrator_syncs_phase():
    """A064: Verify that ARCOrchestrator syncs phase to brain client."""
    brain = AsyncMock()
    brain.current_phase = "unknown"
    
    from agents.arc3.orchestrator import ARCOrchestrator
    orchestrator = ARCOrchestrator(
        brain_client=brain,
        llm_client=MagicMock(),
        session_id="test",
        serializer=MagicMock(),
        config={}
    )
    
    orchestrator.sync_brain_phase("perceive")
    assert brain.current_phase == "perceive"
    
    orchestrator.enter_macro_mode("ACTION6")
    assert brain.current_phase == "macro"
    
    orchestrator.exit_macro_mode("stop")
    assert brain.current_phase == "unknown"
