"""Test A091: HTTP MCP bridge timeout degradation (not crash).

Tests that HTTP bridge timeouts are classified as memory degradation
instead of fatal LLM failures, enabling smoke runs to continue with
degraded state instead of aborting.
"""

import pytest
from agents.arc3.failure_taxonomy import classify_failure, FailureTaxonomy
from sidequest_mcp_client.mcp_brain_client import MCPBrainClient


class TestTransportErrorClassification:
    """Test _classify_mcp_transport_error classification logic."""

    def test_daemon_http_error_recognized(self):
        """HTTP bridge errors are classified as degraded."""
        exc = Exception("daemon_http_error: timed out")
        result = MCPBrainClient._classify_mcp_transport_error(exc)
        
        assert result["status"] == "degraded"
        assert result["memory_degraded"] is True
        assert "timeout" in result["error_code"] or "daemon" in result["error_code"]
        assert result["mcp_transport"] is not None

    def test_connection_refused_recognized(self):
        """Connection refused errors trigger degradation."""
        exc = Exception("Connection refused to 127.0.0.1:5001")
        result = MCPBrainClient._classify_mcp_transport_error(exc)
        
        assert result["status"] == "degraded"
        assert result["memory_degraded"] is True
        assert "connection" in result["error_code"] or "failed" in result["error_code"]

    def test_timed_out_recognized(self):
        """Generic timed out errors trigger degradation."""
        exc = Exception("timed out after 5 seconds")
        result = MCPBrainClient._classify_mcp_transport_error(exc)
        
        assert result["status"] == "degraded"
        assert result["memory_degraded"] is True
        assert result["error_code"] is not None

    def test_daemon_offline_recognized(self):
        """Daemon offline errors are classified as degraded."""
        exc = Exception("daemon_offline: brain service unavailable")
        result = MCPBrainClient._classify_mcp_transport_error(exc)
        
        assert result["status"] == "degraded"
        assert result["memory_degraded"] is True
        assert "daemon_offline" in result["memory_degraded_reason"]


class TestDegradedPayloads:
    """Test degraded payload generation."""

    def test_degraded_read_payload_structure(self):
        """Degraded read payloads have correct empty structure."""
        payload = MCPBrainClient._degraded_read_payload(
            error_code="daemon_http_timeout",
            memory_degraded_reason="daemon_http_timeout",
            mcp_transport="http_bridge"
        )
        
        # Must have empty collections for compatibility
        assert payload["status"] == "degraded"
        assert payload["items"] == []
        assert payload["results"] == []
        assert payload["lessons"] == []
        assert payload["plans"] == []
        assert payload["memory_degraded"] is True
        assert payload["mcp_transport"] == "http_bridge"

    def test_degraded_write_payload_structure(self):
        """Degraded write payloads reject but defer."""
        payload = MCPBrainClient._degraded_write_payload(
            error_code="daemon_http_timeout",
            memory_degraded_reason="daemon_http_timeout",
            mcp_transport="http_bridge"
        )
        
        assert payload["status"] == "degraded"
        assert payload["accepted"] is False
        assert payload["deferred"] is True
        assert payload["memory_degraded"] is True
        assert payload["mcp_transport"] == "http_bridge"


class TestFailureTaxonomyClassification:
    """Test that bridge errors are classified as tool_timeout, not llm_timeout."""

    def test_daemon_http_error_is_tool_timeout(self):
        """Daemon HTTP errors classify as TOOL_TIMEOUT, not LLM_TIMEOUT."""
        exc = Exception("daemon_http_error: connection refused")
        result = classify_failure(exc=exc)
        
        assert result == FailureTaxonomy.TOOL_TIMEOUT

    def test_daemon_offline_is_tool_timeout(self):
        """Daemon offline errors classify as TOOL_TIMEOUT."""
        exc = Exception("daemon_offline: brain service unavailable")
        result = classify_failure(exc=exc)
        
        assert result == FailureTaxonomy.TOOL_TIMEOUT

    def test_daemon_timeout_is_tool_timeout(self):
        """Daemon timeout errors classify as TOOL_TIMEOUT."""
        exc = Exception("daemon_timeout: http bridge timeout")
        result = classify_failure(exc=exc)
        
        assert result == FailureTaxonomy.TOOL_TIMEOUT

    def test_cannot_reach_http_is_tool_timeout(self):
        """Cannot reach HTTP errors classify as TOOL_TIMEOUT."""
        exc = Exception("Cannot reach http://127.0.0.1:5001")
        result = classify_failure(exc=exc)
        
        assert result == FailureTaxonomy.TOOL_TIMEOUT

    def test_mcp_transport_error_is_tool_timeout(self):
        """Generic MCP transport errors are TOOL_TIMEOUT."""
        result = classify_failure(error_message="mcp_transport_error: connection failed")
        
        assert result == FailureTaxonomy.TOOL_TIMEOUT

    def test_generic_timeout_still_llm_timeout(self):
        """Non-transport timeouts still classify as LLM_TIMEOUT."""
        exc = Exception("timeout after 30 seconds")
        result = classify_failure(exc=exc)
        
        # Generic timeout without specific transport error should be LLM_TIMEOUT
        assert result == FailureTaxonomy.LLM_TIMEOUT


class TestClientDegradationTracking:
    """Test that MCPBrainClient tracks memory_degraded state."""

    def test_memory_degraded_flag_set_on_transport_error(self):
        """MCPBrainClient sets memory_degraded when transport error occurs."""
        client = MCPBrainClient()
        
        # Simulate transport error handling
        degradation_info = client._classify_mcp_transport_error(
            Exception("daemon_http_error: timeout")
        )
        
        # Client should detect degradation
        assert degradation_info["memory_degraded"] is True
        assert degradation_info["memory_degraded_reason"] is not None

    def test_degraded_read_preserves_calling_code_compatibility(self):
        """Degraded read payloads have same shape as normal reads for compatibility."""
        degraded = MCPBrainClient._degraded_read_payload(
            "daemon_http_timeout",
            "daemon_http_timeout"
        )
        
        # Calling code expects these fields to be present (empty but present)
        for field in ["items", "results", "lessons", "plans", "procedures"]:
            assert field in degraded
            assert isinstance(degraded[field], list)

    def test_degraded_write_preserves_expected_fields(self):
        """Degraded write payloads have expected fields for compatibility."""
        degraded = MCPBrainClient._degraded_write_payload(
            "daemon_http_timeout",
            "daemon_http_timeout"
        )
        
        # Calling code expects these for success check
        assert "status" in degraded
        assert "accepted" in degraded
        assert "deferred" in degraded


class TestIntegrationWithFailureClassification:
    """Test integration between transport error classification and failure taxonomy."""

    def test_http_bridge_timeout_path(self):
        """Full path: HTTP timeout -> degraded payload -> tool_timeout classification."""
        # Step 1: Transport error detected
        exc = Exception("daemon_http_error: http bridge timed out at 127.0.0.1:5001")
        degradation_info = MCPBrainClient._classify_mcp_transport_error(exc)
        
        assert degradation_info["status"] == "degraded"
        assert "http_bridge" in str(degradation_info.get("mcp_transport", ""))
        
        # Step 2: Failure classified correctly
        failure_class = classify_failure(exc=exc)
        assert failure_class == FailureTaxonomy.TOOL_TIMEOUT

    def test_connection_refused_path(self):
        """Connection refused flows through to tool_timeout."""
        exc = Exception("Connection refused: daemon not listening on 127.0.0.1:5001")
        degradation_info = MCPBrainClient._classify_mcp_transport_error(exc)
        
        assert degradation_info["status"] == "degraded"
        
        failure_class = classify_failure(exc=exc)
        assert failure_class == FailureTaxonomy.TOOL_TIMEOUT

    def test_memory_degraded_reason_preserved(self):
        """Degradation reason flows through all layers."""
        exc = Exception("daemon_offline: HTTP bridge unreachable")
        degradation_info = MCPBrainClient._classify_mcp_transport_error(exc)
        
        reason = degradation_info["memory_degraded_reason"]
        assert reason is not None
        
        # Error message includes reason
        failure_class = classify_failure(
            error_message=reason
        )
        assert failure_class == FailureTaxonomy.TOOL_TIMEOUT
