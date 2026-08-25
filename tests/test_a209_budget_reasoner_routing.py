"""Tests for A209: check_budget routing through the Reasoner.

A209 audits and fixes the gap where check_budget independently ended episodes
without giving the Reasoner a chance to see and record the termination.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from unittest.mock import MagicMock, call

import pytest

from agents.arc4.cycle_policy import check_budget
from agents.arc4.ports import WorkflowDependencies
from agents.arc4.types import (
    EvaluationResult,
    ExecutionResult,
    WorkflowDecision,
    WorkflowRunResult,
    WorkflowState,
    WorkflowStatus,
)
from agents.arc4.workflow import WorkflowLimits, WorkflowOrchestrator


class TestCheckBudgetRoutesToReasoner:
    """Budget exhaustion should invoke the Reasoner (if configured)."""

    def test_budget_fires_with_reasoner_configured(self):
        """When budget exhausts and Reasoner is configured, Reasoner is called."""
        # Setup: state is on cycle 10 of a max 10-cycle budget
        state = WorkflowState(step_index=10)

        # Mock dependencies
        reasoner_mock = MagicMock()
        reasoner_mock.return_value = MagicMock(decision="terminate")

        dependencies = WorkflowDependencies(
            perceive=MagicMock(),
            resolve=MagicMock(),
            plan=MagicMock(),
            vet=MagicMock(),
            execute=MagicMock(),
            evaluate=MagicMock(),
            reason=reasoner_mock,
        )

        orchestrator = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=10))
        observation = {"available_actions": []}

        # Run until budget exhausts
        result = orchestrator.run(state, observation)

        # Verify Reasoner was called (step_index=10 means we're past the budget on step 10)
        # Actually, step_index=10 with max_cycles=10 means the check fires (10 >= 10 is true)
        # So the Reasoner should be called for step_index > 0
        assert reasoner_mock.called, "Reasoner should have been called when budget exhausted"
        assert result.status == WorkflowStatus.BUDGET_EXHAUSTED
        assert result.completed_cycles == 10

    def test_budget_fires_first_cycle_no_reasoner_call(self):
        """First cycle budget exhaustion (max_cycles=0) doesn't call Reasoner."""
        state = WorkflowState(step_index=0)

        reasoner_mock = MagicMock()
        dependencies = WorkflowDependencies(
            perceive=MagicMock(),
            resolve=MagicMock(),
            plan=MagicMock(),
            vet=MagicMock(),
            execute=MagicMock(),
            evaluate=MagicMock(),
            reason=reasoner_mock,
        )

        orchestrator = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=0))
        observation = {"available_actions": []}

        result = orchestrator.run(state, observation)

        # First iteration with max_cycles=0 should NOT call the Reasoner
        # (no prior cycles to report)
        assert not reasoner_mock.called, "Reasoner should not be called on first cycle"
        assert result.status == WorkflowStatus.BUDGET_EXHAUSTED
        assert result.completed_cycles == 0


class TestCheckBudgetWithoutReasoner:
    """When no Reasoner configured, behavior matches the old code (byte-for-byte)."""

    def test_no_reasoner_configured_budget_exhaustion(self):
        """Budget exhaustion without Reasoner returns immediately."""
        state = WorkflowState(step_index=5)

        dependencies = WorkflowDependencies(
            perceive=MagicMock(),
            resolve=MagicMock(),
            plan=MagicMock(),
            vet=MagicMock(),
            execute=MagicMock(),
            evaluate=MagicMock(),
            reason=None,  # No Reasoner
        )

        orchestrator = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=5))
        observation = {"available_actions": []}

        result = orchestrator.run(state, observation)

        assert result.status == WorkflowStatus.BUDGET_EXHAUSTED
        assert result.completed_cycles == 5
        # No phases should have been invoked since budget was checked first
        dependencies.perceive.assert_not_called()


class TestCheckBudgetIsHardCeiling:
    """The hard ceiling is maintained: Reasoner cannot extend past max_cycles."""

    def test_reasoner_response_does_not_override_budget(self):
        """Even if Reasoner said 'continue', episode ends due to budget."""
        state = WorkflowState(step_index=5)

        reasoner_mock = MagicMock()
        # Reasoner tries to say "continue" (this shouldn't happen, but if it does...)
        reasoner_mock.return_value = MagicMock(decision="advance")

        dependencies = WorkflowDependencies(
            perceive=MagicMock(),
            resolve=MagicMock(),
            plan=MagicMock(),
            vet=MagicMock(),
            execute=MagicMock(),
            evaluate=MagicMock(),
            reason=reasoner_mock,
        )

        orchestrator = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=5))
        observation = {"available_actions": []}

        result = orchestrator.run(state, observation)

        # Episode ends as BUDGET_EXHAUSTED regardless of Reasoner's response
        assert result.status == WorkflowStatus.BUDGET_EXHAUSTED
        assert result.completed_cycles == 5
        # Phases should not run after budget is exhausted
        dependencies.perceive.assert_not_called()


class TestCheckBudgetRegressionGuard:
    """Existing budget checks still work as before (regression guard)."""

    def test_budget_check_function_unchanged(self):
        """The check_budget function itself is unchanged."""
        # Budget not exhausted
        assert check_budget(step_index=0, max_cycles=10) is None
        assert check_budget(step_index=5, max_cycles=10) is None
        assert check_budget(step_index=9, max_cycles=10) is None

        # Budget exhausted
        assert check_budget(step_index=10, max_cycles=10) == "budget_exhausted"
        assert check_budget(step_index=11, max_cycles=10) == "budget_exhausted"
        assert check_budget(step_index=0, max_cycles=0) == "budget_exhausted"

    def test_budget_exhaustion_status_reason(self):
        """Budget exhaustion is reported with correct status and reason."""
        state = WorkflowState(step_index=3)

        dependencies = WorkflowDependencies(
            perceive=MagicMock(),
            resolve=MagicMock(),
            plan=MagicMock(),
            vet=MagicMock(),
            execute=MagicMock(),
            evaluate=MagicMock(),
            reason=None,
        )

        orchestrator = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=3))
        observation = {"available_actions": []}

        result = orchestrator.run(state, observation)

        assert result.status == WorkflowStatus.BUDGET_EXHAUSTED
        assert result.reason == "budget_exhausted"
        assert result.completed_cycles == 3


class TestCheckBudgetSyntheticPayloads:
    """Budget exhaustion with Reasoner creates synthetic payloads correctly."""

    def test_synthetic_execution_payload_structure(self):
        """Synthetic execution payload has correct structure for budget exhaustion."""
        # This test verifies that when the Reasoner is called, it receives
        # a well-formed ExecutionResult with candidate=None and the current observation.

        state = WorkflowState(step_index=2)

        reasoner_mock = MagicMock()
        reasoner_mock.return_value = MagicMock(decision="terminate")

        dependencies = WorkflowDependencies(
            perceive=MagicMock(),
            resolve=MagicMock(),
            plan=MagicMock(),
            vet=MagicMock(),
            execute=MagicMock(),
            evaluate=MagicMock(),
            reason=reasoner_mock,
        )

        orchestrator = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=2))
        observation = {"available_actions": ["ACTION1", "ACTION2"]}

        result = orchestrator.run(state, observation)

        # Verify the Reasoner was called
        assert reasoner_mock.called
        call_args = reasoner_mock.call_args
        assert call_args is not None

        # The call should have: state, perception, execution, evaluation, stall_reason
        state_arg, perception_arg, execution_arg, evaluation_arg = call_args[0]
        stall_reason = call_args[1].get("stall_reason")

        # Execution should have candidate=None (nothing was attempted)
        assert execution_arg.candidate is None
        assert execution_arg.observation == observation

        # Evaluation should have meaningful_progress=False
        assert evaluation_arg.meaningful_progress is False
        assert evaluation_arg.reason == "budget_exhausted"

        # Stall reason should indicate budget
        assert stall_reason == "budget_exhausted"


class TestCheckBudgetEdgeCases:
    """Edge cases for budget exhaustion."""

    def test_max_cycles_zero_first_iteration(self):
        """max_cycles=0 ends immediately on first cycle."""
        state = WorkflowState(step_index=0)

        reasoner_mock = MagicMock()
        dependencies = WorkflowDependencies(
            perceive=MagicMock(),
            resolve=MagicMock(),
            plan=MagicMock(),
            vet=MagicMock(),
            execute=MagicMock(),
            evaluate=MagicMock(),
            reason=reasoner_mock,
        )

        orchestrator = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=0))
        observation = {"available_actions": []}

        result = orchestrator.run(state, observation)

        assert result.status == WorkflowStatus.BUDGET_EXHAUSTED
        assert result.completed_cycles == 0
        # Reasoner not called on first iteration
        assert not reasoner_mock.called

    def test_budget_fires_before_phases(self):
        """Budget check fires before any phases run."""
        state = WorkflowState(step_index=5)

        # All phase mocks
        perceive_mock = MagicMock()
        resolve_mock = MagicMock()
        plan_mock = MagicMock()
        vet_mock = MagicMock()
        execute_mock = MagicMock()
        evaluate_mock = MagicMock()
        reasoner_mock = MagicMock()
        reasoner_mock.return_value = MagicMock(decision="terminate")

        dependencies = WorkflowDependencies(
            perceive=perceive_mock,
            resolve=resolve_mock,
            plan=plan_mock,
            vet=vet_mock,
            execute=execute_mock,
            evaluate=evaluate_mock,
            reason=reasoner_mock,
        )

        orchestrator = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=5))
        observation = {"available_actions": []}

        result = orchestrator.run(state, observation)

        # None of the normal phases should run
        perceive_mock.assert_not_called()
        resolve_mock.assert_not_called()
        plan_mock.assert_not_called()
        vet_mock.assert_not_called()
        execute_mock.assert_not_called()
        evaluate_mock.assert_not_called()

        # But the Reasoner should be called (since step_index > 0)
        reasoner_mock.assert_called()
        assert result.status == WorkflowStatus.BUDGET_EXHAUSTED
