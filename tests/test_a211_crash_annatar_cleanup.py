"""A211 tests: Crash handler should attempt best-effort thread closure before returning CRASHED.

The non-negotiable invariant: if the close-out attempt raises, the original crash's
traceback must still be the one reported, never masked by a secondary failure.
"""

import traceback
import pytest
from unittest.mock import Mock, MagicMock, patch, call

from agents.arc4.workflow import WorkflowOrchestrator
from agents.arc4.ports import WorkflowDependencies
from agents.arc4.types import (
    WorkflowState,
    WorkflowStatus,
    PhaseResult,
    PhaseStatus,
    WorkflowPhase,
    PerceptionSnapshot,
)


def make_failing_perceive():
    """Create a perceive phase that raises an exception."""
    def perceive(state, observation):
        raise RuntimeError("Simulated perceive failure")
    return perceive


def make_mock_perception():
    """Create a minimal valid perception payload."""
    return PerceptionSnapshot(
        grid_hash="test_hash",
        observation={},
        grid_shape=(5, 5),
        loop_signal=False,
        repeated_grid_count=0,
        entities=(),
        metadata={},
    )


class TestCrashWithoutInvestigationThread:
    """Test crash when no investigation thread is open (regression guard)."""

    def test_crash_no_thread_no_cleanup_call(self):
        """No investigation thread open -> no cleanup call, pure CRASHED result."""
        # Create a minimal state with no active investigation thread
        state = WorkflowState()
        state.active_investigation_anchor = None  # No investigation thread

        # Mock dependencies: perceive will raise
        perceive = make_failing_perceive()
        resolve = Mock(return_value=PhaseResult(phase=WorkflowPhase.RESOLVE, status=PhaseStatus.OK))
        plan = Mock(return_value=PhaseResult(phase=WorkflowPhase.PLAN, status=PhaseStatus.OK))
        vet = Mock(return_value=PhaseResult(phase=WorkflowPhase.VET, status=PhaseStatus.OK))
        execute = Mock(return_value=PhaseResult(phase=WorkflowPhase.EXECUTE, status=PhaseStatus.OK))
        evaluate = Mock(return_value=PhaseResult(phase=WorkflowPhase.EVALUATE, status=PhaseStatus.OK))
        annatar = Mock()
        on_crash_cleanup = Mock()

        dependencies = WorkflowDependencies(
            perceive=perceive,
            resolve=resolve,
            plan=plan,
            vet=vet,
            execute=execute,
            evaluate=evaluate,
            annatar=annatar,
            on_crash_cleanup=on_crash_cleanup,
        )

        orchestrator = WorkflowOrchestrator(dependencies)
        result = orchestrator.run(state, {})

        # Should still be CRASHED
        assert result.status == WorkflowStatus.CRASHED
        # No cleanup call should be made (no thread active)
        on_crash_cleanup.assert_not_called()
        # Original crash traceback should be present
        assert result.traceback is not None
        assert "Simulated perceive failure" in result.traceback


class TestCrashWithNullAnnatar:
    """Test crash when no Annatar is configured (regression guard)."""

    def test_crash_no_annatar_no_cleanup_call(self):
        """No Annatar configured -> no cleanup call, pure CRASHED result."""
        state = WorkflowState()
        state.active_investigation_anchor = {
            "thread_id": "thread_123",
            "anchor_ref": "goal_1",
            "anchor_type": "goal",
            "state": "exploring",
        }

        perceive = make_failing_perceive()
        resolve = Mock(return_value=PhaseResult(phase=WorkflowPhase.RESOLVE, status=PhaseStatus.OK))
        plan = Mock(return_value=PhaseResult(phase=WorkflowPhase.PLAN, status=PhaseStatus.OK))
        vet = Mock(return_value=PhaseResult(phase=WorkflowPhase.VET, status=PhaseStatus.OK))
        execute = Mock(return_value=PhaseResult(phase=WorkflowPhase.EXECUTE, status=PhaseStatus.OK))
        evaluate = Mock(return_value=PhaseResult(phase=WorkflowPhase.EVALUATE, status=PhaseStatus.OK))
        on_crash_cleanup = Mock()

        dependencies = WorkflowDependencies(
            perceive=perceive,
            resolve=resolve,
            plan=plan,
            vet=vet,
            execute=execute,
            evaluate=evaluate,
            annatar=None,  # No Annatar
            on_crash_cleanup=on_crash_cleanup,
        )

        orchestrator = WorkflowOrchestrator(dependencies)
        result = orchestrator.run(state, {})

        # Should still be CRASHED
        assert result.status == WorkflowStatus.CRASHED
        # No cleanup call (Annatar not configured)
        on_crash_cleanup.assert_not_called()


class TestCrashWithValidThread:
    """Test crash when a valid investigation thread is open and Annatar is configured."""

    def test_crash_with_thread_cleanup_called(self):
        """Crash + thread open + Annatar configured -> cleanup call made before CRASHED."""
        state = WorkflowState()
        state.active_investigation_anchor = {
            "thread_id": "thread_123",
            "anchor_ref": "goal_1",
            "anchor_type": "goal",
            "state": "exploring",
        }

        perceive = make_failing_perceive()
        resolve = Mock(return_value=PhaseResult(phase=WorkflowPhase.RESOLVE, status=PhaseStatus.OK))
        plan = Mock(return_value=PhaseResult(phase=WorkflowPhase.PLAN, status=PhaseStatus.OK))
        vet = Mock(return_value=PhaseResult(phase=WorkflowPhase.VET, status=PhaseStatus.OK))
        execute = Mock(return_value=PhaseResult(phase=WorkflowPhase.EXECUTE, status=PhaseStatus.OK))
        evaluate = Mock(return_value=PhaseResult(phase=WorkflowPhase.EVALUATE, status=PhaseStatus.OK))
        annatar = Mock()
        on_crash_cleanup = Mock()

        dependencies = WorkflowDependencies(
            perceive=perceive,
            resolve=resolve,
            plan=plan,
            vet=vet,
            execute=execute,
            evaluate=evaluate,
            annatar=annatar,
            on_crash_cleanup=on_crash_cleanup,
        )

        orchestrator = WorkflowOrchestrator(dependencies)
        result = orchestrator.run(state, {})

        # Should be CRASHED
        assert result.status == WorkflowStatus.CRASHED
        # Cleanup should have been called with thread_id and "exhausted" state
        on_crash_cleanup.assert_called_once_with("thread_123", "exhausted")
        # Original crash traceback should be present
        assert result.traceback is not None
        assert "Simulated perceive failure" in result.traceback


class TestCrashCleanupRaisesDoesNotMaskOriginal:
    """The non-negotiable invariant: cleanup failure must never mask the original crash."""

    def test_cleanup_raises_original_traceback_preserved(self):
        """If cleanup itself raises, original crash traceback must still be reported."""
        original_exception_msg = "Original perceive failure"
        cleanup_exception_msg = "Cleanup failed"

        state = WorkflowState()
        state.active_investigation_anchor = {
            "thread_id": "thread_123",
            "anchor_ref": "goal_1",
            "anchor_type": "goal",
            "state": "exploring",
        }

        perceive = make_failing_perceive()
        perceive.__name__ = "perceive"
        # Replace the function to raise our specific message
        def perceive_that_raises(*args, **kwargs):
            raise RuntimeError(original_exception_msg)
        perceive_that_raises.__name__ = "perceive"
        perceive = perceive_that_raises

        resolve = Mock(return_value=PhaseResult(phase=WorkflowPhase.RESOLVE, status=PhaseStatus.OK))
        plan = Mock(return_value=PhaseResult(phase=WorkflowPhase.PLAN, status=PhaseStatus.OK))
        vet = Mock(return_value=PhaseResult(phase=WorkflowPhase.VET, status=PhaseStatus.OK))
        execute = Mock(return_value=PhaseResult(phase=WorkflowPhase.EXECUTE, status=PhaseStatus.OK))
        evaluate = Mock(return_value=PhaseResult(phase=WorkflowPhase.EVALUATE, status=PhaseStatus.OK))
        annatar = Mock()

        # Cleanup function that raises
        def on_crash_cleanup(thread_id, state_value):
            raise RuntimeError(cleanup_exception_msg)

        dependencies = WorkflowDependencies(
            perceive=perceive,
            resolve=resolve,
            plan=plan,
            vet=vet,
            execute=execute,
            evaluate=evaluate,
            annatar=annatar,
            on_crash_cleanup=on_crash_cleanup,
        )

        orchestrator = WorkflowOrchestrator(dependencies)
        result = orchestrator.run(state, {})

        # Should still be CRASHED
        assert result.status == WorkflowStatus.CRASHED
        # Traceback must contain the ORIGINAL exception, not the cleanup one
        assert result.traceback is not None
        assert original_exception_msg in result.traceback, \
            f"Original exception not in traceback. Got: {result.traceback}"
        assert cleanup_exception_msg not in result.traceback, \
            f"Cleanup exception should not be in traceback. Got: {result.traceback}"


class TestCrashCleanupWithMissingThreadId:
    """Defensive handling: malformed anchor dict should not cause cleanup to raise."""

    def test_crash_anchor_missing_thread_id_no_cleanup(self):
        """Anchor dict without thread_id -> no cleanup call (defensive)."""
        state = WorkflowState()
        state.active_investigation_anchor = {
            # Missing "thread_id"
            "anchor_ref": "goal_1",
            "anchor_type": "goal",
            "state": "exploring",
        }

        perceive = make_failing_perceive()
        resolve = Mock(return_value=PhaseResult(phase=WorkflowPhase.RESOLVE, status=PhaseStatus.OK))
        plan = Mock(return_value=PhaseResult(phase=WorkflowPhase.PLAN, status=PhaseStatus.OK))
        vet = Mock(return_value=PhaseResult(phase=WorkflowPhase.VET, status=PhaseStatus.OK))
        execute = Mock(return_value=PhaseResult(phase=WorkflowPhase.EXECUTE, status=PhaseStatus.OK))
        evaluate = Mock(return_value=PhaseResult(phase=WorkflowPhase.EVALUATE, status=PhaseStatus.OK))
        annatar = Mock()
        on_crash_cleanup = Mock()

        dependencies = WorkflowDependencies(
            perceive=perceive,
            resolve=resolve,
            plan=plan,
            vet=vet,
            execute=execute,
            evaluate=evaluate,
            annatar=annatar,
            on_crash_cleanup=on_crash_cleanup,
        )

        orchestrator = WorkflowOrchestrator(dependencies)
        result = orchestrator.run(state, {})

        # Should be CRASHED
        assert result.status == WorkflowStatus.CRASHED
        # No cleanup call (missing thread_id)
        on_crash_cleanup.assert_not_called()


class TestCrashCleanupNullThreadId:
    """Defensive handling: None thread_id should not cause cleanup to raise."""

    def test_crash_anchor_with_null_thread_id_no_cleanup(self):
        """Anchor with thread_id=None -> no cleanup call (defensive)."""
        state = WorkflowState()
        state.active_investigation_anchor = {
            "thread_id": None,  # None thread_id
            "anchor_ref": "goal_1",
            "anchor_type": "goal",
            "state": "exploring",
        }

        perceive = make_failing_perceive()
        resolve = Mock(return_value=PhaseResult(phase=WorkflowPhase.RESOLVE, status=PhaseStatus.OK))
        plan = Mock(return_value=PhaseResult(phase=WorkflowPhase.PLAN, status=PhaseStatus.OK))
        vet = Mock(return_value=PhaseResult(phase=WorkflowPhase.VET, status=PhaseStatus.OK))
        execute = Mock(return_value=PhaseResult(phase=WorkflowPhase.EXECUTE, status=PhaseStatus.OK))
        evaluate = Mock(return_value=PhaseResult(phase=WorkflowPhase.EVALUATE, status=PhaseStatus.OK))
        annatar = Mock()
        on_crash_cleanup = Mock()

        dependencies = WorkflowDependencies(
            perceive=perceive,
            resolve=resolve,
            plan=plan,
            vet=vet,
            execute=execute,
            evaluate=evaluate,
            annatar=annatar,
            on_crash_cleanup=on_crash_cleanup,
        )

        orchestrator = WorkflowOrchestrator(dependencies)
        result = orchestrator.run(state, {})

        # Should be CRASHED
        assert result.status == WorkflowStatus.CRASHED
        # No cleanup call (None thread_id)
        on_crash_cleanup.assert_not_called()


class TestCrashWithNullOnCrashCleanup:
    """Defensive handling: no on_crash_cleanup provided."""

    def test_crash_with_null_cleanup_callable_no_crash(self):
        """on_crash_cleanup=None -> no crash in crash handler."""
        state = WorkflowState()
        state.active_investigation_anchor = {
            "thread_id": "thread_123",
            "anchor_ref": "goal_1",
            "anchor_type": "goal",
            "state": "exploring",
        }

        perceive = make_failing_perceive()
        resolve = Mock(return_value=PhaseResult(phase=WorkflowPhase.RESOLVE, status=PhaseStatus.OK))
        plan = Mock(return_value=PhaseResult(phase=WorkflowPhase.PLAN, status=PhaseStatus.OK))
        vet = Mock(return_value=PhaseResult(phase=WorkflowPhase.VET, status=PhaseStatus.OK))
        execute = Mock(return_value=PhaseResult(phase=WorkflowPhase.EXECUTE, status=PhaseStatus.OK))
        evaluate = Mock(return_value=PhaseResult(phase=WorkflowPhase.EVALUATE, status=PhaseStatus.OK))
        annatar = Mock()

        dependencies = WorkflowDependencies(
            perceive=perceive,
            resolve=resolve,
            plan=plan,
            vet=vet,
            execute=execute,
            evaluate=evaluate,
            annatar=annatar,
            on_crash_cleanup=None,  # No cleanup callable
        )

        orchestrator = WorkflowOrchestrator(dependencies)
        result = orchestrator.run(state, {})

        # Should be CRASHED (not crash in crash handler)
        assert result.status == WorkflowStatus.CRASHED
        assert result.traceback is not None
