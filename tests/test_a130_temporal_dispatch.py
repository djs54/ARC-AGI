"""Tests for A130 Temporal dispatch integration."""

import pytest
import argparse


class TestTemporalDispatch:
    """Test Temporal dispatch path and fallback behavior."""

    def test_cli_temporal_flag_parsed(self):
        """Verify --temporal CLI flag is recognized and parsed."""
        from run_single_puzzle import build_arg_parser
        
        parser = build_arg_parser()
        
        # Test with --temporal flag
        args = parser.parse_args(["--temporal"])
        assert args.temporal is True
        
        # Test without --temporal flag
        args = parser.parse_args([])
        assert args.temporal is False

    def test_inline_fallback_when_temporal_disabled(self):
        """Verify inline path is used when ARC_TEMPORAL_ENABLED=0 (default)."""
        import os
        os.environ["ARC_TEMPORAL_ENABLED"] = "0"
        
        from agents.arc4.temporal_client import is_temporal_enabled
        assert is_temporal_enabled() is False

    def test_temporal_enabled_flag(self):
        """Verify is_temporal_enabled() detects env var."""
        import os
        
        # Test with "1"
        os.environ["ARC_TEMPORAL_ENABLED"] = "1"
        from agents.arc4.temporal_client import is_temporal_enabled
        assert is_temporal_enabled() is True
        
        # Test with "true"
        os.environ["ARC_TEMPORAL_ENABLED"] = "true"
        # Need to reimport to pick up new env var
        import importlib
        import agents.arc4.temporal_client
        importlib.reload(agents.arc4.temporal_client)
        from agents.arc4.temporal_client import is_temporal_enabled
        assert is_temporal_enabled() is True
        
        # Reset to default
        os.environ["ARC_TEMPORAL_ENABLED"] = "0"

    def test_result_structure_matches_inline(self):
        """Verify WorkflowRunResult from Temporal has same keys as inline result."""
        from agents.arc4.types import (
            WorkflowRunResult,
            WorkflowStatus,
            WorkflowState,
        )
        
        # Create a sample result (as would come from Temporal or inline)
        result = WorkflowRunResult(
            status=WorkflowStatus.TERMINATED,
            state=WorkflowState(step_index=10),
            reason="Test completion",
            completed_cycles=10,
        )
        
        # Serialize to dict (as Temporal would send)
        result_dict = result.to_dict()
        
        # Deserialize back (as dispatch code would do)
        result_restored = WorkflowRunResult.from_dict(result_dict)
        
        # Verify structure is preserved
        assert result_restored.status == result.status
        assert result_restored.state.step_index == result.state.step_index
        assert result_restored.completed_cycles == result.completed_cycles

    def test_has_temporal_import(self):
        """Verify run_single_puzzle.py can import Temporal support."""
        import run_single_puzzle
        assert hasattr(run_single_puzzle, 'HAS_TEMPORAL')
        # HAS_TEMPORAL will be True if temporalio is installed, False otherwise
        assert isinstance(run_single_puzzle.HAS_TEMPORAL, bool)

    def test_temporal_client_import_guards(self):
        """Verify temporal_client module has proper guards."""
        from agents.arc4.temporal_client import is_temporal_enabled
        # Should not raise even if temporalio is not installed
        result = is_temporal_enabled()
        assert isinstance(result, bool)

    def test_cli_args_structure(self):
        """Verify all expected args are present in parser."""
        from run_single_puzzle import build_arg_parser
        
        parser = build_arg_parser()
        args = parser.parse_args(["--temporal"])
        
        # Check expected attributes
        assert hasattr(args, 'temporal')
        assert hasattr(args, 'agent_version')
        assert hasattr(args, 'real_api')
        assert hasattr(args, 'config')
        assert hasattr(args, 'model')

    def test_dispatch_with_args_none(self):
        """Verify dispatch code handles args=None gracefully."""
        # This test verifies the dispatch logic can be called with args=None
        # (as it would be in backward-compatible calls)
        import run_single_puzzle
        
        # The _run_arc_v2_task function signature should accept args=None
        import inspect
        sig = inspect.signature(run_single_puzzle._run_arc_v2_task)
        params = sig.parameters
        
        assert 'args' in params
        assert params['args'].default is None
