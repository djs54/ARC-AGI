"""A204: write-ahead cycle recording bracketing the `execute` phase call,
and startup resume / real-observation reconciliation.

Highest-stakes card in the trajectory-Reasoner family (A200-A206): the one
place a bug means double-acting on the real, live ARC API. See
docs/superpowers/specs/2026-08-23-trajectory-reasoner-design.md section 7.

Test groups:
  - TestWriteAheadBracketing: agents/arc4/workflow.py::wrap_execute_with_write_ahead,
    exercised through a real WorkflowOrchestrator.run() so ordering and
    resilience are proven at the same seam A202/A203 already test through.
  - TestResumeOrStartAttempt: arc_runtime/dispatch.py::resume_or_start_attempt,
    the startup crash-injection reconciliation logic (spec section 7 items 1-3).
  - TestEffectVisibleInObservation: the small conservative helper.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.types import WorkflowDecision, WorkflowState, WorkflowStatus
from agents.arc4.workflow import WorkflowLimits, WorkflowOrchestrator, wrap_execute_with_write_ahead
from arc_runtime.dispatch import _effect_visible_in_observation, resume_or_start_attempt

# Reuse the existing WorkflowOrchestrator regression fixtures rather than
# inventing new ones -- same helpers test_a202/test_a203 already lean on.
from test_arc4_workflow import (
    _dependencies as _shared_dependencies,
    _evaluation,
)


def _anchor(thread_id: str | None = "thread-1") -> dict:
    return {
        "anchor_ref": "goal-1",
        "anchor_type": "goal",
        "thread_id": thread_id,
        "state": "exploring",
        "deepening_cycle_count": 0,
        "already_retried": False,
    }


def _terminal_deps(calls: list[str]):
    return _shared_dependencies(
        calls,
        overrides={"evaluate": [_evaluation(WorkflowDecision.TERMINATE, meaningful_progress=True, reason="done")]},
    )


# ── Write-ahead bracketing (agents/arc4/workflow.py) ────────────────────


class TestWriteAheadBracketing:
    def test_write_cycle_called_before_execute_with_action_sent_true(self):
        """Test 1: write_cycle appears before execute in call order, not
        just 'both were called'."""
        calls: list[str] = []
        graph_port = MagicMock()

        def _write_cycle(thread_id, step, action_sent):
            calls.append("write_cycle")
            assert action_sent is True
            return {"cycle_id": "c1"}

        graph_port.write_cycle.side_effect = _write_cycle
        graph_port.confirm_cycle.side_effect = lambda *a, **k: calls.append("confirm_cycle")

        deps = _terminal_deps(calls)
        deps.execute = wrap_execute_with_write_ahead(deps.execute, graph_port)

        state = WorkflowState(active_investigation_anchor=_anchor())
        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(state, {"grid": [[1]]})

        assert calls.index("write_cycle") < calls.index("execute")
        graph_port.write_cycle.assert_called_once_with("thread-1", 0, action_sent=True)
        assert result.status == WorkflowStatus.TERMINATED

    def test_confirm_cycle_called_after_execute_with_write_cycle_cycle_id(self):
        """Test 2: confirm_cycle fires after execute returns, using the
        exact cycle_id write_cycle produced."""
        calls: list[str] = []
        graph_port = MagicMock()
        graph_port.write_cycle.side_effect = lambda *a, **k: (calls.append("write_cycle"), {"cycle_id": "c-xyz"})[1]
        graph_port.confirm_cycle.side_effect = lambda *a, **k: calls.append("confirm_cycle")

        deps = _terminal_deps(calls)
        deps.execute = wrap_execute_with_write_ahead(deps.execute, graph_port)

        state = WorkflowState(active_investigation_anchor=_anchor())
        WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(state, {"grid": [[1]]})

        assert calls.index("execute") < calls.index("confirm_cycle")
        graph_port.confirm_cycle.assert_called_once_with("c-xyz", decision="pending", confirmed=True)

    def test_write_cycle_exception_does_not_block_execute(self):
        """Test 3 (the non-negotiable invariant): write_cycle raising must
        never prevent execute from running, and run() must complete
        normally. Confirmed this fails against a naive (no try/except)
        implementation before the try/except was added -- see Resolution."""
        calls: list[str] = []
        graph_port = MagicMock()
        graph_port.write_cycle.side_effect = RuntimeError("graph unreachable")

        deps = _terminal_deps(calls)
        deps.execute = wrap_execute_with_write_ahead(deps.execute, graph_port)

        state = WorkflowState(active_investigation_anchor=_anchor())
        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(state, {"grid": [[1]]})

        assert "execute" in calls
        assert result.status == WorkflowStatus.TERMINATED
        graph_port.confirm_cycle.assert_not_called()

    def test_confirm_cycle_exception_does_not_crash_a_successful_execution(self):
        calls: list[str] = []
        graph_port = MagicMock()
        graph_port.write_cycle.return_value = {"cycle_id": "c1"}
        graph_port.confirm_cycle.side_effect = RuntimeError("graph unreachable")

        deps = _terminal_deps(calls)
        deps.execute = wrap_execute_with_write_ahead(deps.execute, graph_port)

        state = WorkflowState(active_investigation_anchor=_anchor())
        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(state, {"grid": [[1]]})

        assert result.status == WorkflowStatus.TERMINATED

    def test_no_active_thread_skips_write_cycle_entirely(self):
        """state.active_investigation_anchor is None (no thread currently
        active) -- write-ahead must be a clean no-op, not an error."""
        calls: list[str] = []
        graph_port = MagicMock()

        deps = _terminal_deps(calls)
        deps.execute = wrap_execute_with_write_ahead(deps.execute, graph_port)

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(WorkflowState(), {"grid": [[1]]})

        graph_port.write_cycle.assert_not_called()
        graph_port.confirm_cycle.assert_not_called()
        assert result.status == WorkflowStatus.TERMINATED

    def test_no_graph_port_is_pure_passthrough_regression_guard(self):
        """Test 8: mirrors every prior card's regression guard -- no
        graph_port configured means write-ahead wrapping is a complete
        no-op and run() behaves exactly as before A204."""
        calls: list[str] = []
        deps = _terminal_deps(calls)
        deps.execute = wrap_execute_with_write_ahead(deps.execute, None)

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(WorkflowState(), {"grid": [[1]]})

        assert result.status == WorkflowStatus.TERMINATED
        assert "execute" in calls

    def test_unwrapped_execute_dependency_is_returned_untouched_when_no_write_cycle_attr(self):
        """graph_port present but doesn't expose write_cycle/confirm_cycle
        (e.g. a partial fake) -- must degrade cleanly, not raise."""
        calls: list[str] = []

        class _BareGraphPort:
            pass

        deps = _terminal_deps(calls)
        deps.execute = wrap_execute_with_write_ahead(deps.execute, _BareGraphPort())

        state = WorkflowState(active_investigation_anchor=_anchor())
        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(state, {"grid": [[1]]})

        assert result.status == WorkflowStatus.TERMINATED
        assert "execute" in calls


# ── Startup resume + real-observation reconciliation (arc_runtime/dispatch.py) ──


class TestResumeOrStartAttempt:
    def test_resumed_false_no_prior_thread_skips_reconciliation(self):
        """Test 6."""
        graph_port = MagicMock()
        graph_port.start_or_resume_thread.return_value = {
            "thread_id": "t1",
            "state": "exploring",
            "resumed": False,
            "last_cycle": None,
        }
        fetch = MagicMock()

        result = resume_or_start_attempt("task-1", graph_port, fetch)

        assert result["resumed"] is False
        fetch.assert_not_called()
        graph_port.confirm_cycle.assert_not_called()

    def test_start_or_resume_thread_raising_falls_back_to_fresh_start(self):
        """Test 7 (part A)."""
        graph_port = MagicMock()
        graph_port.start_or_resume_thread.side_effect = RuntimeError("graph down")
        fetch = MagicMock()

        result = resume_or_start_attempt("task-1", graph_port, fetch)

        assert result == {"resumed": False, "step_index": 0, "thread_id": None, "real_observation": None}
        fetch.assert_not_called()

    def test_graph_port_missing_start_or_resume_method_falls_back(self):
        """Test 7 (part B)."""

        class _NoStartOrResume:
            pass

        fetch = MagicMock()

        result = resume_or_start_attempt("task-1", _NoStartOrResume(), fetch)

        assert result["resumed"] is False
        fetch.assert_not_called()

    def test_graph_port_none_falls_back_without_touching_fetch(self):
        fetch = MagicMock()
        result = resume_or_start_attempt("task-1", None, fetch)
        assert result["resumed"] is False
        fetch.assert_not_called()

    def test_crash_injection_branch_a_action_landed_confirms_true(self):
        """Test 4: action actually landed -- confirm_cycle(confirmed=True),
        and never any graph interaction that would re-send the action."""
        graph_port = MagicMock()
        graph_port.start_or_resume_thread.return_value = {
            "thread_id": "t1",
            "state": "exploring",
            "resumed": True,
            "last_cycle": {"action_sent": True, "action_confirmed_by_observation": False, "cycle_id": "c1", "step": 3},
        }
        real_observation = {"frame": [[1, 2], [3, 4]], "state": "NOT_FINISHED"}
        fetch = MagicMock(return_value=real_observation)

        result = resume_or_start_attempt("task-1", graph_port, fetch)

        fetch.assert_called_once()
        graph_port.confirm_cycle.assert_called_once_with("c1", decision="resumed", confirmed=True)
        graph_port.write_cycle.assert_not_called()  # never the action-sending path
        assert result["resumed"] is True
        assert result["step_index"] == 3
        assert result["real_observation"] == real_observation

    def test_crash_injection_branch_b_action_never_landed_confirms_false(self):
        """Test 5: action never landed -- confirm_cycle(confirmed=False),
        and the resumed state allows a fresh attempt at that step."""
        graph_port = MagicMock()
        graph_port.start_or_resume_thread.return_value = {
            "thread_id": "t1",
            "state": "exploring",
            "resumed": True,
            "last_cycle": {"action_sent": True, "action_confirmed_by_observation": False, "cycle_id": "c1", "step": 3},
        }
        fetch = MagicMock(return_value=None)  # could not retrieve real observation

        result = resume_or_start_attempt("task-1", graph_port, fetch)

        fetch.assert_called_once()
        graph_port.confirm_cycle.assert_called_once_with("c1", decision="resumed", confirmed=False)
        graph_port.write_cycle.assert_not_called()
        assert result["resumed"] is True
        assert result["step_index"] == 3

    def test_already_confirmed_cycle_skips_reconciliation_entirely(self):
        """last_cycle.action_confirmed_by_observation already True -- no
        ambiguity, no need to touch the real API at all."""
        graph_port = MagicMock()
        graph_port.start_or_resume_thread.return_value = {
            "thread_id": "t1",
            "state": "exploring",
            "resumed": True,
            "last_cycle": {"action_sent": True, "action_confirmed_by_observation": True, "cycle_id": "c1", "step": 5},
        }
        fetch = MagicMock()

        result = resume_or_start_attempt("task-1", graph_port, fetch)

        fetch.assert_not_called()
        graph_port.confirm_cycle.assert_not_called()
        assert result["step_index"] == 5

    def test_no_action_sent_in_last_cycle_skips_reconciliation(self):
        graph_port = MagicMock()
        graph_port.start_or_resume_thread.return_value = {
            "thread_id": "t1",
            "state": "exploring",
            "resumed": True,
            "last_cycle": {"action_sent": False, "action_confirmed_by_observation": False, "cycle_id": "c1", "step": 2},
        }
        fetch = MagicMock()

        result = resume_or_start_attempt("task-1", graph_port, fetch)

        fetch.assert_not_called()
        graph_port.confirm_cycle.assert_not_called()

    def test_confirm_cycle_exception_does_not_propagate(self):
        graph_port = MagicMock()
        graph_port.start_or_resume_thread.return_value = {
            "thread_id": "t1",
            "state": "exploring",
            "resumed": True,
            "last_cycle": {"action_sent": True, "action_confirmed_by_observation": False, "cycle_id": "c1", "step": 1},
        }
        graph_port.confirm_cycle.side_effect = RuntimeError("graph write failed")
        fetch = MagicMock(return_value={"frame": [[0]]})

        result = resume_or_start_attempt("task-1", graph_port, fetch)

        assert result["resumed"] is True  # confirm_cycle failure doesn't crash the resume path

    def test_fetch_real_observation_failure_propagates_rather_than_guessing(self):
        """Deliberate design choice (documented in Resolution): if the real
        API cannot be reached during the ambiguous reconciliation window,
        this function refuses to silently guess confirmed=True/False in
        either direction -- it lets the failure propagate so the caller
        treats attempt startup as blocked, since guessing either way risks
        the exact failure mode this card exists to prevent."""
        graph_port = MagicMock()
        graph_port.start_or_resume_thread.return_value = {
            "thread_id": "t1",
            "state": "exploring",
            "resumed": True,
            "last_cycle": {"action_sent": True, "action_confirmed_by_observation": False, "cycle_id": "c1", "step": 1},
        }
        fetch = MagicMock(side_effect=RuntimeError("ARC API unreachable"))

        with pytest.raises(RuntimeError, match="ARC API unreachable"):
            resume_or_start_attempt("task-1", graph_port, fetch)

        graph_port.confirm_cycle.assert_not_called()


class TestEffectVisibleInObservation:
    def test_returns_true_when_observation_retrieved(self):
        assert _effect_visible_in_observation({"cycle_id": "c1"}, {"frame": [[1]]}) is True

    def test_returns_false_when_observation_is_none(self):
        assert _effect_visible_in_observation({"cycle_id": "c1"}, None) is False
