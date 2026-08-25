"""Tests for A180: `classify_v2_termination`'s mapping dict has no entry for
`WorkflowStatus.SKIPPED`'s "second_veto" reason or for `WorkflowStatus.TERMINATED`
itself, so both silently fall through to the `FailureTaxonomy.CRASH` default --
confirmed live 2026-08-07: a clean 3-step `make smoke` run with no exception
anywhere in its logs was labeled `failure_class=crash`. See backlog/A180.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.evaluator import classify_v2_termination
from agents.common.failure_taxonomy import FailureTaxonomy


class TestSecondVetoIsNotCrash:
    def test_second_veto_maps_to_strategy_exhausted(self):
        result = classify_v2_termination("skipped", "second_veto")
        assert result == FailureTaxonomy.STRATEGY_EXHAUSTED.value
        assert result != FailureTaxonomy.CRASH.value


class TestCrashedStatusIsExplicit:
    def test_crashed_status_with_unrecognized_reason_still_maps_to_crash(self):
        # Not just an accident of the default -- crashed should be an explicit
        # entry so a future default change can't silently break this.
        assert classify_v2_termination("crashed", "some_new_reason_not_yet_seen") == FailureTaxonomy.CRASH.value


class TestTerminatedIsNotCrashByDefault:
    def test_generic_terminated_reason_is_not_crash(self):
        result = classify_v2_termination("terminated", "terminated")
        assert result != FailureTaxonomy.CRASH.value

    def test_terminated_status_with_unrecognized_reason_is_not_crash(self):
        # workflow.py's TERMINATED path always supplies a reason, but the
        # status-level fallback must still not default to crash for it.
        result = classify_v2_termination("terminated", "some_new_evaluator_reason")
        assert result != FailureTaxonomy.CRASH.value

    def test_reasoner_exhausted_reports_strategy_exhausted_not_crash(self):
        """Explicit case for the reason string workflow.py's
        `if outcome.decision == "terminate": return self._finish(...,
        WorkflowStatus.TERMINATED, "reasoner_exhausted", ...)` produces
        (agents/arc4/reasoner_signals.py's whole-episode-futility fix,
        2026-08-25) -- not in the mapping dict by name, so it falls through
        to the "terminated" status-level entry (STRATEGY_EXHAUSTED), not the
        CRASH default. This path was previously unreachable in production
        (decision_for_state() never returned "terminate") and is now live
        for the first time, so pin its classification explicitly rather
        than relying only on the generic unrecognized-reason test above."""
        result = classify_v2_termination("terminated", "reasoner_exhausted")
        assert result == FailureTaxonomy.STRATEGY_EXHAUSTED.value


class TestSolvedReasonsAreSuccessNotFailure:
    """evaluator.py::_terminal_reason can surface "solved"/"victory" as the
    TERMINATED reason when the game's own observation/metadata says so --
    these are wins, not failures, and must not carry any FailureTaxonomy
    value (mirroring the existing None-mapped intent of "completed"/"won",
    which never actually match any real reason string workflow.py emits)."""

    def test_solved_is_not_a_failure(self):
        assert classify_v2_termination("terminated", "solved") is None

    def test_victory_is_not_a_failure(self):
        assert classify_v2_termination("terminated", "victory") is None


class TestActionSpaceExhaustedIsStrategyExhausted:
    def test_action_space_exhausted_maps_to_strategy_exhausted(self):
        assert classify_v2_termination("terminated", "action_space_exhausted") == FailureTaxonomy.STRATEGY_EXHAUSTED.value


class TestExistingBehaviorPreserved:
    """Regression guard: entries that were already correct must not change."""

    def test_stalled_status_unchanged(self):
        assert classify_v2_termination("stalled", "stall_detected") == FailureTaxonomy.STRATEGY_EXHAUSTED.value

    def test_budget_exhausted_unchanged(self):
        assert classify_v2_termination("budget_exhausted", "budget_exhausted") == FailureTaxonomy.WALL_CLOCK_TIMEOUT.value

    def test_crash_reason_unchanged(self):
        assert classify_v2_termination("crashed", "crash") == FailureTaxonomy.CRASH.value

    def test_crash_in_evaluate_unchanged(self):
        assert classify_v2_termination("crashed", "crash_in_evaluate") == FailureTaxonomy.CRASH.value

    def test_exception_path_unchanged(self):
        try:
            raise ValueError("boom")
        except ValueError as exc:
            assert classify_v2_termination("crashed", "crash", exception=exc) == FailureTaxonomy.CRASH.value


class TestEveryRealWorkflowTerminationPathIsMapped:
    """Parametrized over every (status, reason) pair agents/arc4/workflow.py's
    self._finish(...) call sites can actually produce -- audited directly from
    the source (see backlog/plans/A-180-*.md's Technical approach step 1).
    None of these are crashes; only the explicit exception path is."""

    @pytest.mark.parametrize(
        "status,reason",
        [
            ("budget_exhausted", "budget_exhausted"),
            ("skipped", "second_veto"),
            ("stalled", "stall_detected"),
            ("terminated", "terminated"),
            ("terminated", "action_space_exhausted"),
            ("terminated", "terminal"),
            ("terminated", "done"),
            ("terminated", "game_over"),
        ],
    )
    def test_non_crash_paths_never_return_crash(self, status, reason):
        assert classify_v2_termination(status, reason) != FailureTaxonomy.CRASH.value

    def test_only_the_exception_path_and_explicit_crash_reasons_return_crash(self):
        assert classify_v2_termination("crashed", "crash") == FailureTaxonomy.CRASH.value
