"""Tests for A209: check_budget routing through Annatar.

A209 audits and fixes the gap where check_budget independently ended episodes
without giving Annatar a chance to see and record the termination.
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


class TestCheckBudgetRoutesToAnnatar:
    """A252 update: budget exhaustion no longer invokes Annatar at all --
    it closes out any open investigation thread directly via
    on_crash_cleanup instead (see tests/test_a252_budget_exhausted_honest_close_out.py
    for full coverage of that new close-out path). This class's name
    predates that change; kept for historical continuity of the remaining
    (still-accurate) test below."""

    # A252 note: test_budget_fires_with_annatar_configured deleted here.
    # It asserted `annatar_mock.called` for step_index=10/max_cycles=10 with
    # no active_investigation_anchor set on state -- a premise this card
    # removes entirely (Annatar is no longer invoked by the budget path at
    # all, regardless of step_index). Confirmed via TDD: the assertion
    # failed ("Expected 'mock' to have been called") once the fix landed.
    # The same scenario (budget exhausted, no anchor, Annatar never
    # called) is covered without loss by
    # test_a252_budget_exhausted_honest_close_out.py::
    # TestBudgetExhaustedWithNoOpenThread::test_no_anchor_no_cleanup_call.

    def test_budget_fires_first_cycle_no_annatar_call(self):
        """First cycle budget exhaustion (max_cycles=0) doesn't call Annatar."""
        state = WorkflowState(step_index=0)

        annatar_mock = MagicMock()
        dependencies = WorkflowDependencies(
            perceive=MagicMock(),
            resolve=MagicMock(),
            plan=MagicMock(),
            vet=MagicMock(),
            execute=MagicMock(),
            evaluate=MagicMock(),
            annatar=annatar_mock,
        )

        orchestrator = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=0))
        observation = {"available_actions": []}

        result = orchestrator.run(state, observation)

        # First iteration with max_cycles=0 should NOT call Annatar
        # (no prior cycles to report)
        assert not annatar_mock.called, "Annatar should not be called on first cycle"
        assert result.status == WorkflowStatus.BUDGET_EXHAUSTED
        assert result.completed_cycles == 0


# A250 note: this file used to also carry a TestCheckBudgetWithoutAnnatar
# class ("When no Annatar configured, behavior matches the old code
# (byte-for-byte)") pinning `_route_budget_through_annatar`'s
# `if self._dependencies.annatar is None: return BUDGET_EXHAUSTED` branch
# directly. That branch was deleted by A250 -- `annatar` is unconditionally
# wired in production since A202, so it was permanently dead code (confirmed
# via TDD: the test raised TypeError, "'NoneType' object is not callable",
# once the branch was removed, since annatar is no longer allowed to be
# None at all). TestCheckBudgetRoutesToAnnatar (below/above),
# TestCheckBudgetIsHardCeiling, TestCheckBudgetSyntheticPayloads, and
# TestCheckBudgetEdgeCases already cover the same underlying mechanism
# (budget exhaustion ends the episode as BUDGET_EXHAUSTED, with or without
# Annatar invoking) with Annatar configured, so no coverage was lost.


class TestCheckBudgetIsHardCeiling:
    """The hard ceiling is maintained: Annatar cannot extend past max_cycles."""

    def test_annatar_response_does_not_override_budget(self):
        """Even if Annatar somehow said 'continue', episode ends due to
        budget. A252 update: the budget path no longer invokes Annatar at
        all (it closes out any open thread directly via on_crash_cleanup
        instead), which makes the hard-ceiling guarantee even stronger than
        the original "Annatar's decision is inspected but ignored" framing
        this test was written against -- there's nothing left to ignore.
        `annatar_mock` is still configured here (WorkflowDependencies
        requires it) and still primed with a would-be "advance" response,
        to prove that response never influences the outcome even though it
        is now provably never called at all."""
        state = WorkflowState(step_index=5)

        annatar_mock = MagicMock()
        # Annatar would try to say "continue" if it were ever asked -- it isn't.
        annatar_mock.return_value = MagicMock(decision="advance")

        dependencies = WorkflowDependencies(
            perceive=MagicMock(),
            resolve=MagicMock(),
            plan=MagicMock(),
            vet=MagicMock(),
            execute=MagicMock(),
            evaluate=MagicMock(),
            annatar=annatar_mock,
        )

        orchestrator = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=5))
        observation = {"available_actions": []}

        result = orchestrator.run(state, observation)

        # Episode ends as BUDGET_EXHAUSTED regardless of Annatar's response
        assert result.status == WorkflowStatus.BUDGET_EXHAUSTED
        assert result.completed_cycles == 5
        # A252: Annatar is never invoked by the budget path at all now.
        annatar_mock.assert_not_called()
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
        """Budget exhaustion is reported with correct status and reason.
        A252 update: this no longer depends on Annatar being invoked at all
        (it never is, for the budget path) -- `annatar_mock` here is purely
        incidental scaffolding (WorkflowDependencies requires a real
        `annatar` callable) and irrelevant to what this test actually
        verifies."""
        state = WorkflowState(step_index=3)

        annatar_mock = MagicMock()
        annatar_mock.return_value = MagicMock(decision="terminate")

        dependencies = WorkflowDependencies(
            perceive=MagicMock(),
            resolve=MagicMock(),
            plan=MagicMock(),
            vet=MagicMock(),
            execute=MagicMock(),
            evaluate=MagicMock(),
            annatar=annatar_mock,
        )

        orchestrator = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=3))
        observation = {"available_actions": []}

        result = orchestrator.run(state, observation)

        assert result.status == WorkflowStatus.BUDGET_EXHAUSTED
        assert result.reason == "budget_exhausted"
        assert result.completed_cycles == 3


# A252 note: this file used to also carry a TestCheckBudgetSyntheticPayloads
# class ("Budget exhaustion with Annatar creates synthetic payloads
# correctly") with a single test, test_synthetic_execution_payload_structure,
# that called orchestrator.run(), asserted annatar_mock.called, and inspected
# annatar_mock.call_args for the exact synthetic PerceptionSnapshot/
# ExecutionResult/EvaluationResult shape the pre-fix
# _route_budget_through_annatar constructed. A252 deletes that synthetic-
# payload construction entirely (see backlog/A252.md) -- there is no longer
# any Annatar call, and therefore no synthetic payload shape left to test.
# Confirmed via TDD: `assert annatar_mock.called` failed once the fix
# landed. No coverage was lost -- what replaced the synthetic-payload
# mechanism (the direct on_crash_cleanup(thread_id, "exhausted") close-out
# call) is now covered by tests/test_a252_budget_exhausted_honest_close_out.py.


class TestCheckBudgetEdgeCases:
    """Edge cases for budget exhaustion."""

    def test_max_cycles_zero_first_iteration(self):
        """max_cycles=0 ends immediately on first cycle."""
        state = WorkflowState(step_index=0)

        annatar_mock = MagicMock()
        dependencies = WorkflowDependencies(
            perceive=MagicMock(),
            resolve=MagicMock(),
            plan=MagicMock(),
            vet=MagicMock(),
            execute=MagicMock(),
            evaluate=MagicMock(),
            annatar=annatar_mock,
        )

        orchestrator = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=0))
        observation = {"available_actions": []}

        result = orchestrator.run(state, observation)

        assert result.status == WorkflowStatus.BUDGET_EXHAUSTED
        assert result.completed_cycles == 0
        # Annatar not called on first iteration
        assert not annatar_mock.called

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
        annatar_mock = MagicMock()
        annatar_mock.return_value = MagicMock(decision="terminate")

        dependencies = WorkflowDependencies(
            perceive=perceive_mock,
            resolve=resolve_mock,
            plan=plan_mock,
            vet=vet_mock,
            execute=execute_mock,
            evaluate=evaluate_mock,
            annatar=annatar_mock,
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

        # A252: Annatar is never invoked by the budget path at all now
        # (regardless of step_index) -- the close-out mechanism no longer
        # routes through it.
        annatar_mock.assert_not_called()
        assert result.status == WorkflowStatus.BUDGET_EXHAUSTED
