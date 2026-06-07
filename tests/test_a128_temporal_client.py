"""Tests for A128 Temporal infrastructure."""

import os
import pytest


def test_temporal_disabled_by_default():
    """Verify is_temporal_enabled() returns False by default."""
    # Ensure env var is not set
    old_value = os.environ.pop("ARC_TEMPORAL_ENABLED", None)
    try:
        from agents.arc4.temporal_client import is_temporal_enabled
        assert is_temporal_enabled() is False
    finally:
        if old_value is not None:
            os.environ["ARC_TEMPORAL_ENABLED"] = old_value


@pytest.mark.asyncio
async def test_start_workflow_returns_none_when_disabled():
    """Verify start_arc_workflow() returns None without trying to connect."""
    # Ensure temporal is disabled
    old_value = os.environ.pop("ARC_TEMPORAL_ENABLED", None)
    try:
        from agents.arc4.temporal_client import start_arc_workflow
        result = await start_arc_workflow("test-task", {}, {})
        assert result is None
    finally:
        if old_value is not None:
            os.environ["ARC_TEMPORAL_ENABLED"] = old_value


def test_temporal_imports_optional():
    """Verify importing agents.arc4 works without temporalio installed."""
    # This test verifies that arc4 can be imported without temporalio
    # The temporal modules are only imported on-demand (lazy loading)
    try:
        from agents.arc4 import workflow
        assert workflow is not None
    except ImportError:
        pytest.fail("agents.arc4 should import without temporalio")
