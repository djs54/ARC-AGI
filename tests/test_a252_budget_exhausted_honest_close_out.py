"""A252 tests: budget-exhausted close-out should be honest, not a synthetic
Annatar cycle.

`_route_budget_through_annatar` previously ran a full synthetic Annatar
cycle (fabricated PerceptionSnapshot/ExecutionResult/EvaluationResult
through the real `self._dependencies.annatar(...)`) purely to trigger its
own end-of-cycle `write_thread_state` bookkeeping -- a graph write computed
from entirely fabricated data, plus an occasional real LLM call if the open
thread happened to be AWAITING_LLM. This card replaces that with A211's own
direct `on_crash_cleanup(thread_id, "exhausted")` close-out pattern, no
synthetic Annatar cycle at all. See backlog/A252.md.
"""

from __future__ import annotations

from unittest.mock import Mock

from agents.arc4.ports import WorkflowDependencies
from agents.arc4.types import (
    PhaseResult,
    PhaseStatus,
    WorkflowPhase,
    WorkflowState,
    WorkflowStatus,
)
from agents.arc4.workflow import WorkflowLimits, WorkflowOrchestrator


def _phase_ok_mocks():
    return {
        "perceive": Mock(return_value=PhaseResult(phase=WorkflowPhase.PERCEIVE, status=PhaseStatus.OK)),
        "resolve": Mock(return_value=PhaseResult(phase=WorkflowPhase.RESOLVE, status=PhaseStatus.OK)),
        "plan": Mock(return_value=PhaseResult(phase=WorkflowPhase.PLAN, status=PhaseStatus.OK)),
        "vet": Mock(return_value=PhaseResult(phase=WorkflowPhase.VET, status=PhaseStatus.OK)),
        "execute": Mock(return_value=PhaseResult(phase=WorkflowPhase.EXECUTE, status=PhaseStatus.OK)),
        "evaluate": Mock(return_value=PhaseResult(phase=WorkflowPhase.EVALUATE, status=PhaseStatus.OK)),
    }


class TestBudgetExhaustedWithOpenThreadCallsCleanupDirectly:
    """The core fix: an open thread gets a direct on_crash_cleanup call,
    never a synthetic Annatar cycle."""

    def test_open_thread_calls_cleanup_annatar_never_invoked(self):
        state = WorkflowState(step_index=5)
        state.active_investigation_anchor = {
            "thread_id": "thread_abc",
            "anchor_ref": "goal_1",
            "anchor_type": "goal",
            "state": "exploring",
        }

        annatar_mock = Mock()
        on_crash_cleanup = Mock()

        dependencies = WorkflowDependencies(
            **_phase_ok_mocks(),
            annatar=annatar_mock,
            on_crash_cleanup=on_crash_cleanup,
        )

        orchestrator = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=5))
        result = orchestrator.run(state, {"available_actions": []})

        assert result.status == WorkflowStatus.BUDGET_EXHAUSTED
        assert result.reason == "budget_exhausted"
        on_crash_cleanup.assert_called_once_with("thread_abc", "exhausted")
        annatar_mock.assert_not_called()


class TestBudgetExhaustedWithNoOpenThread:
    """No open investigation thread -> nothing to close out, no cleanup call."""

    def test_no_anchor_no_cleanup_call(self):
        state = WorkflowState(step_index=5)
        state.active_investigation_anchor = None

        annatar_mock = Mock()
        on_crash_cleanup = Mock()

        dependencies = WorkflowDependencies(
            **_phase_ok_mocks(),
            annatar=annatar_mock,
            on_crash_cleanup=on_crash_cleanup,
        )

        orchestrator = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=5))
        result = orchestrator.run(state, {"available_actions": []})

        assert result.status == WorkflowStatus.BUDGET_EXHAUSTED
        on_crash_cleanup.assert_not_called()
        annatar_mock.assert_not_called()

    def test_anchor_with_null_thread_id_no_cleanup_call(self):
        state = WorkflowState(step_index=5)
        state.active_investigation_anchor = {
            "thread_id": None,
            "anchor_ref": "goal_1",
            "anchor_type": "goal",
            "state": "exploring",
        }

        annatar_mock = Mock()
        on_crash_cleanup = Mock()

        dependencies = WorkflowDependencies(
            **_phase_ok_mocks(),
            annatar=annatar_mock,
            on_crash_cleanup=on_crash_cleanup,
        )

        orchestrator = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=5))
        result = orchestrator.run(state, {"available_actions": []})

        assert result.status == WorkflowStatus.BUDGET_EXHAUSTED
        on_crash_cleanup.assert_not_called()
        annatar_mock.assert_not_called()


class TestBudgetExhaustedCleanupRaisesDoesNotBlockResult:
    """Non-negotiable: a cleanup failure must never prevent BUDGET_EXHAUSTED
    from returning, mirroring A211's own crash-path regression guard."""

    def test_cleanup_raises_still_ends_budget_exhausted(self):
        state = WorkflowState(step_index=5)
        state.active_investigation_anchor = {
            "thread_id": "thread_abc",
            "anchor_ref": "goal_1",
            "anchor_type": "goal",
            "state": "exploring",
        }

        annatar_mock = Mock()

        def on_crash_cleanup(thread_id, state_value):
            raise RuntimeError("cleanup blew up")

        dependencies = WorkflowDependencies(
            **_phase_ok_mocks(),
            annatar=annatar_mock,
            on_crash_cleanup=on_crash_cleanup,
        )

        orchestrator = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=5))
        result = orchestrator.run(state, {"available_actions": []})

        assert result.status == WorkflowStatus.BUDGET_EXHAUSTED
        assert result.reason == "budget_exhausted"
        annatar_mock.assert_not_called()


class TestBudgetExhaustedWithNullOnCrashCleanup:
    """Defensive: on_crash_cleanup=None must not crash the budget path."""

    def test_null_cleanup_callable_no_crash(self):
        state = WorkflowState(step_index=5)
        state.active_investigation_anchor = {
            "thread_id": "thread_abc",
            "anchor_ref": "goal_1",
            "anchor_type": "goal",
            "state": "exploring",
        }

        annatar_mock = Mock()

        dependencies = WorkflowDependencies(
            **_phase_ok_mocks(),
            annatar=annatar_mock,
            on_crash_cleanup=None,
        )

        orchestrator = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=5))
        result = orchestrator.run(state, {"available_actions": []})

        assert result.status == WorkflowStatus.BUDGET_EXHAUSTED
        annatar_mock.assert_not_called()


class TestBudgetExhaustedFirstIterationUnchanged:
    """step_index=0 has no prior cycle to close out -- unchanged behavior:
    ends immediately, no cleanup attempted, Annatar not invoked."""

    def test_first_iteration_no_cleanup_no_annatar(self):
        state = WorkflowState(step_index=0)
        state.active_investigation_anchor = {
            "thread_id": "thread_abc",
            "anchor_ref": "goal_1",
            "anchor_type": "goal",
            "state": "exploring",
        }

        annatar_mock = Mock()
        on_crash_cleanup = Mock()

        dependencies = WorkflowDependencies(
            **_phase_ok_mocks(),
            annatar=annatar_mock,
            on_crash_cleanup=on_crash_cleanup,
        )

        orchestrator = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=0))
        result = orchestrator.run(state, {"available_actions": []})

        assert result.status == WorkflowStatus.BUDGET_EXHAUSTED
        assert result.completed_cycles == 0
        on_crash_cleanup.assert_not_called()
        annatar_mock.assert_not_called()
