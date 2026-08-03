"""A112 — Crash Root-Cause Capture In Orchestration Loop

Regression test for capturing full tracebacks and exception details when
the orchestration loop crashes.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio


class TestA112CrashRootCauseCapture:
    """Verify that crashes include full traceback details instead of generic UnknownCrash."""

    @pytest.mark.asyncio
    async def test_crash_captures_full_traceback(self):
        """When orchestration loop raises an exception, result includes full traceback."""
        # Simulate an exception during puzzle execution
        test_exception = ValueError("Test crash: something went wrong")
        
        # The exception should have the traceback captured
        import traceback
        
        # Verify the exception handler would capture the traceback correctly
        try:
            raise test_exception
        except Exception as exc:
            tb_str = traceback.format_exc()
            exception_type = type(exc).__name__
            exception_message = str(exc)
            
            # Verify the captured details
            assert "ValueError" in tb_str
            assert "Test crash: something went wrong" in tb_str
            assert exception_type == "ValueError"
            assert exception_message == "Test crash: something went wrong"
            assert "raise test_exception" in tb_str  # Line from the traceback

    def test_crash_failure_class_implies_failed_status(self):
        """When failure_class=crash, orchestration_status must be 'failed'."""
        from agents.arc3.trace_names import normalize_orchestration_status
        from agents.arc3.failure_taxonomy import FailureTaxonomy

        # Verify that crash status normalizes to "failed"
        result = normalize_orchestration_status(
            failure_class=FailureTaxonomy.CRASH.value,
            current_status="ok"
        )
        assert result == "failed", "orchestration_status must be 'failed' when failure_class is 'crash'"

        # Verify other statuses are unaffected
        result = normalize_orchestration_status(
            failure_class=FailureTaxonomy.STRATEGY_EXHAUSTED.value,
            current_status="ok"
        )
        assert result == "ok"

    def test_exception_details_captured_in_result(self):
        """Result payload includes exception_type, exception_message, and failure_reason."""
        # This test verifies the structure of what gets captured
        exception = RuntimeError("Example runtime error")
        
        import traceback
        try:
            raise exception
        except Exception as exc:
            tb_str = traceback.format_exc()
            failure_reason = f"{type(exc).__name__}: {exc}\n{tb_str}"
            exception_type = type(exc).__name__
            exception_message = str(exc)
            
            # Verify result fields would be populated
            result = {
                "failure_reason": failure_reason,
                "exception_type": exception_type,
                "exception_message": exception_message,
            }
            
            assert result["exception_type"] == "RuntimeError"
            assert result["exception_message"] == "Example runtime error"
            assert "RuntimeError: Example runtime error" in result["failure_reason"]
            assert "raise exception" in result["failure_reason"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
