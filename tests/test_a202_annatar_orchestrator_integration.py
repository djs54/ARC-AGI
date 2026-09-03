"""A202: wires A200's pure state machine + A201's graph client into
WorkflowOrchestrator.run() via the new `annatar` dependency.

Test groups:
  - Backward-compat byte-for-byte regression (annatar=None) against a real
    pre-A202 baseline of workflow.py, loaded from a copy of the file taken
    immediately before this card's edits.
  - Orchestrator control-flow tests: terminate / repeat_deepen / advance /
    stall-folded-into-annatar / termination-short-circuits-before-annatar /
    check_budget unaffected.
  - Unit tests for agents/arc4/annatar_signals.py's compute_cycle_signals
    and run_annatar_cycle (including the AWAITING_LLM -> resolve_llm_vote
    -> apply_llm_vote path and the NotImplementedError placeholder).
"""

from __future__ import annotations

import importlib.util
import sys
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4 import annatar_signals as annatar_signals_module
from agents.arc4.annatar_state_machine import CycleSignals, InvestigationState
from agents.arc4.ports import WorkflowDependencies
from agents.arc4.annatar_signals import compute_cycle_signals, resolve_llm_vote, run_annatar_cycle
from agents.arc4.types import (
    EvaluationResult,
    ExecutionResult,
    GoalHypothesis,
    PerceptionSnapshot,
    PhaseResult,
    PhaseStatus,
    PlanCandidate,
    PlanningResult,
    AnnatarOutcome,
    ResolvedGoal,
    VetDecision,
    WorkflowDecision,
    WorkflowPhase,
    WorkflowState,
    WorkflowStatus,
)
from agents.arc4.workflow import WorkflowLimits, WorkflowOrchestrator

# Reuse the existing WorkflowOrchestrator regression fixtures rather than
# inventing new ones (per the plan's explicit instruction) -- these are the
# exact same scripted-phase helpers every other workflow.py test relies on.
from test_arc4_workflow import (
    _dependencies as _shared_dependencies,
    _evaluation,
    _execute,
    _goal,
    _perception,
    _plan,
    _scripted_phase,
    _vet,
)


# ── Baseline (pre-A202) WorkflowOrchestrator, loaded from a real snapshot ──
# taken immediately before this card's edits to agents/arc4/workflow.py, so
# test 1 below compares against genuine pre-change behavior, not a
# hand-typed guess at what it used to do.
#
# Fixed 2026-08-25 (A207 follow-up): this originally pointed at a path
# inside one specific agent session's own /private/tmp scratchpad directory
# -- it happened to work in every session run so far only because that
# scratchpad file was never cleaned up, but it was never actually committed
# to the repo. A fresh clone, a different machine, or real CI (which is
# exactly what caught this: `make test-a`'s fixed file list never touches
# this test, so only `make test-all`/full CI runs ever exercised the
# collection failure) would hit `FileNotFoundError` on collection, taking
# down every test in this file. Moved into the repo proper so the fixture
# travels with the code that depends on it.
_BASELINE_PATH = Path(__file__).resolve().parent / "fixtures" / "workflow_pre_a202_baseline.py"


def _load_baseline_orchestrator_module():
    spec = importlib.util.spec_from_file_location("agents.arc4._workflow_baseline_a202", _BASELINE_PATH)
    module = importlib.util.module_from_spec(spec)
    # The baseline file uses relative imports (`from .cycle_policy import
    # ...`); set __package__ so those resolve against the real,
    # already-importable agents.arc4 package on disk (cycle_policy.py/
    # ports.py/types.py are unmodified-in-structure by this card, only
    # extended with new optional fields, so the baseline's relative imports
    # keep working).
    module.__package__ = "agents.arc4"
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_baseline = _load_baseline_orchestrator_module()


def _run_both(overrides=None, limits_kwargs=None):
    """Run the same scripted scenario through both the baseline (pre-A202)
    orchestrator and the current one, with reason=None on the current side,
    and return (baseline_result, current_result)."""
    limits_kwargs = limits_kwargs or {}
    calls_baseline: list[str] = []
    calls_current: list[str] = []

    baseline_deps = _shared_dependencies(calls_baseline, overrides=overrides)
    current_deps = _shared_dependencies(calls_current, overrides=overrides)

    baseline_orchestrator = _baseline.WorkflowOrchestrator(
        baseline_deps, limits=_baseline.WorkflowLimits(**limits_kwargs)
    )
    current_orchestrator = WorkflowOrchestrator(current_deps, limits=WorkflowLimits(**limits_kwargs))

    baseline_result = baseline_orchestrator.run(WorkflowState(), {"grid": [[1]]})
    current_result = current_orchestrator.run(WorkflowState(), {"grid": [[1]]})
    return baseline_result, current_result


class TestBackwardCompatByteForByte:
    """Test 1: WorkflowDependencies(reason=None, ...) must produce
    byte-for-byte identical WorkflowOrchestrator.run() output to the real
    pre-A202 baseline, for at least two existing scenario shapes."""

    def test_simple_terminate_scenario_matches_baseline(self):
        baseline_result, current_result = _run_both(limits_kwargs={"max_cycles": 3})

        assert current_result.status == baseline_result.status == WorkflowStatus.TERMINATED
        assert current_result.to_dict() == baseline_result.to_dict()

    def test_stall_scenario_matches_baseline(self):
        overrides = {
            "perceive": [_perception("grid-1"), _perception("grid-2")],
            "resolve": [_goal(), _goal()],
            "plan": [_plan(), _plan()],
            "vet": [_vet(True), _vet(True)],
            "execute": [_execute(grid_hash="grid-2"), _execute(grid_hash="grid-3")],
            "evaluate": [
                _evaluation(WorkflowDecision.CONTINUE, meaningful_progress=False, reason="flat", falsification_delta=1),
                _evaluation(WorkflowDecision.CONTINUE, meaningful_progress=False, reason="flat again", falsification_delta=1),
            ],
        }
        baseline_result, current_result = _run_both(
            overrides=overrides, limits_kwargs={"max_cycles": 5, "max_consecutive_no_progress": 2}
        )

        assert current_result.status == baseline_result.status == WorkflowStatus.STALLED
        assert current_result.to_dict() == baseline_result.to_dict()


class TestAnnatarControlFlow:
    def test_terminate_decision_ends_run_as_annatar_exhausted(self):
        calls: list[str] = []
        mock_reason = MagicMock(return_value=AnnatarOutcome(decision="terminate"))
        deps = _shared_dependencies(
            calls,
            overrides={"evaluate": [_evaluation(WorkflowDecision.CONTINUE, meaningful_progress=False, reason="flat")]},
        )
        deps.annatar = mock_reason

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(WorkflowState(), {"grid": [[1]]})

        assert result.status == WorkflowStatus.TERMINATED
        assert result.reason == "annatar_exhausted"
        assert mock_reason.call_count == 1

    def test_repeat_deepen_sets_anchor_hint_and_continues(self):
        calls: list[str] = []
        outcomes = deque(
            [
                AnnatarOutcome(decision="repeat_deepen", anchor_ref="g1", anchor_type="goal"),
                None,  # unused: cycle 2 terminates via evaluation, reason not called again
            ]
        )
        mock_reason = MagicMock(side_effect=lambda *a, **k: outcomes.popleft())
        deps = _shared_dependencies(
            calls,
            overrides={
                "perceive": [_perception("grid-1"), _perception("grid-2")],
                "resolve": [_goal(), _goal()],
                "plan": [_plan(), _plan()],
                "vet": [_vet(True), _vet(True)],
                "execute": [_execute(grid_hash="grid-2"), _execute(grid_hash="grid-3")],
                "evaluate": [
                    _evaluation(WorkflowDecision.CONTINUE, meaningful_progress=False, reason="flat"),
                    _evaluation(WorkflowDecision.TERMINATE, meaningful_progress=True, reason="done"),
                ],
            },
        )
        deps.annatar = mock_reason

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=5)).run(WorkflowState(), {"grid": [[1]]})

        assert result.status == WorkflowStatus.TERMINATED
        assert result.completed_cycles == 2
        assert mock_reason.call_count == 1
        assert result.state.annatar_anchor_hint is not None
        assert result.state.annatar_anchor_hint.decision == "repeat_deepen"

    def test_advance_decision_clears_anchor_hint(self):
        calls: list[str] = []
        mock_reason = MagicMock(return_value=AnnatarOutcome(decision="advance"))
        deps = _shared_dependencies(
            calls,
            overrides={"evaluate": [_evaluation(WorkflowDecision.CONTINUE, meaningful_progress=False, reason="flat")]},
        )
        deps.annatar = mock_reason

        state = WorkflowState(annatar_anchor_hint=AnnatarOutcome(decision="repeat_deepen"))
        # Give it a second cycle's worth of scripted responses so the loop
        # can genuinely continue past cycle 1 rather than crashing on an
        # exhausted scripted queue.
        deps = _shared_dependencies(
            calls,
            overrides={
                "perceive": [_perception("grid-1"), _perception("grid-2")],
                "resolve": [_goal(), _goal()],
                "plan": [_plan(), _plan()],
                "vet": [_vet(True), _vet(True)],
                "execute": [_execute(grid_hash="grid-2"), _execute(grid_hash="grid-3")],
                "evaluate": [
                    _evaluation(WorkflowDecision.CONTINUE, meaningful_progress=False, reason="flat"),
                    _evaluation(WorkflowDecision.TERMINATE, meaningful_progress=True, reason="done"),
                ],
            },
        )
        deps.annatar = mock_reason

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=5)).run(state, {"grid": [[1]]})

        assert result.completed_cycles == 2
        assert result.state.annatar_anchor_hint is None

    def test_stall_signal_folds_into_annatar_instead_of_independently_stalling(self):
        calls: list[str] = []
        outcomes = deque(
            [
                AnnatarOutcome(decision="repeat_deepen"),
                AnnatarOutcome(decision="terminate"),
            ]
        )
        mock_reason = MagicMock(side_effect=lambda *a, **k: outcomes.popleft())
        deps = _shared_dependencies(
            calls,
            overrides={
                "perceive": [_perception("grid-1"), _perception("grid-2")],
                "resolve": [_goal(), _goal()],
                "plan": [_plan(), _plan()],
                "vet": [_vet(True), _vet(True)],
                "execute": [_execute(grid_hash="grid-2"), _execute(grid_hash="grid-3")],
                "evaluate": [
                    _evaluation(WorkflowDecision.CONTINUE, meaningful_progress=False, reason="flat", falsification_delta=1),
                    _evaluation(WorkflowDecision.CONTINUE, meaningful_progress=False, reason="flat again", falsification_delta=1),
                ],
            },
        )
        deps.annatar = mock_reason

        result = WorkflowOrchestrator(
            deps, limits=WorkflowLimits(max_cycles=5, max_consecutive_no_progress=2)
        ).run(WorkflowState(), {"grid": [[1]]})

        # Old standalone path would have returned STALLED here (see
        # test_stall_terminates_after_repeated_no_progress in
        # test_arc4_workflow.py for the same scenario without an annatar).
        # With an annatar configured, the run must NOT end via that path.
        assert result.status != WorkflowStatus.STALLED
        assert mock_reason.call_count == 2
        first_call_kwargs = mock_reason.call_args_list[0].kwargs
        second_call_kwargs = mock_reason.call_args_list[1].kwargs
        assert first_call_kwargs["stall_reason"] is None
        assert second_call_kwargs["stall_reason"] == "stall_detected"
        assert result.status == WorkflowStatus.TERMINATED
        assert result.reason == "annatar_exhausted"

    def test_evaluation_termination_short_circuits_before_annatar_runs(self):
        calls: list[str] = []
        mock_reason = MagicMock(return_value=AnnatarOutcome(decision="advance"))
        deps = _shared_dependencies(
            calls,
            overrides={"evaluate": [_evaluation(WorkflowDecision.TERMINATE, meaningful_progress=True, reason="done")]},
        )
        deps.annatar = mock_reason

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(WorkflowState(), {"grid": [[1]]})

        assert result.status == WorkflowStatus.TERMINATED
        assert result.reason == "done"
        assert mock_reason.call_count == 0

    def test_check_budget_still_gates_before_anything_else(self):
        calls: list[str] = []
        mock_reason = MagicMock(return_value=AnnatarOutcome(decision="advance"))
        deps = _shared_dependencies(calls)
        deps.annatar = mock_reason

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=0)).run(WorkflowState(), {"grid": [[1]]})

        assert calls == []
        assert result.status == WorkflowStatus.BUDGET_EXHAUSTED
        assert mock_reason.call_count == 0


class TestSecondVetoRoutesThroughAnnatar:
    """User-directed follow-up (2026-08-25): a double veto previously ended
    the episode directly via _finish(SKIPPED, "second_veto", ...) without
    ever invoking Annatar -- exactly the strategic moment ("the safety
    layer just rejected our plan twice, what now") the "one agent that sees
    everything end-to-end" is supposed to own, structurally excluded from
    it. `vet` fires before `execute`/`evaluate`, so no real
    ExecutionResult/EvaluationResult exists for a second-veto cycle;
    workflow.py now feeds Annatar a synthetic "nothing was attempted"
    pair (candidate=None, meaningful_progress=False) plus
    stall_reason="second_veto" (reusing the existing stall-fold mechanism
    rather than inventing a parallel one)."""

    def test_no_annatar_configured_preserves_exact_prior_behavior(self):
        """Regression guard, mirrors test_second_veto_skips_execution in
        test_arc4_workflow.py exactly -- reason=None must be untouched."""
        calls: list[str] = []
        deps = _shared_dependencies(
            calls,
            overrides={
                "vet": [_vet(False, reason="first veto"), _vet(False, reason="second veto")],
                "plan": [_plan(), _plan()],
                "resolve": [_goal(), _goal()],
            },
        )

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(WorkflowState(), {"grid": [[1]]})

        assert result.status == WorkflowStatus.SKIPPED
        assert result.reason == "second_veto"

    def test_annatar_invoked_with_synthetic_inconclusive_signals_and_second_veto_stall_reason(self):
        calls: list[str] = []
        mock_reason = MagicMock(return_value=AnnatarOutcome(decision="terminate"))
        deps = _shared_dependencies(
            calls,
            overrides={
                "vet": [_vet(False, reason="first veto"), _vet(False, reason="second veto")],
                "plan": [_plan(), _plan()],
                "resolve": [_goal(), _goal()],
            },
        )
        deps.annatar = mock_reason

        WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(WorkflowState(), {"grid": [[1]]})

        assert mock_reason.call_count == 1
        call = mock_reason.call_args_list[0]
        assert call.kwargs["stall_reason"] == "second_veto"
        synthetic_execution, synthetic_evaluation = call.args[2], call.args[3]
        assert synthetic_execution.candidate is None
        assert synthetic_evaluation.meaningful_progress is False

    def test_terminate_decision_ends_run_as_annatar_exhausted(self):
        calls: list[str] = []
        mock_reason = MagicMock(return_value=AnnatarOutcome(decision="terminate"))
        deps = _shared_dependencies(
            calls,
            overrides={
                "vet": [_vet(False, reason="first veto"), _vet(False, reason="second veto")],
                "plan": [_plan(), _plan()],
                "resolve": [_goal(), _goal()],
            },
        )
        deps.annatar = mock_reason

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(WorkflowState(), {"grid": [[1]]})

        assert result.status == WorkflowStatus.TERMINATED
        assert result.reason == "annatar_exhausted"

    def test_repeat_decision_continues_the_loop_to_a_fresh_cycle_instead_of_ending_the_episode(self):
        calls: list[str] = []
        mock_reason = MagicMock(return_value=AnnatarOutcome(decision="repeat_deepen", anchor_ref="g1", anchor_type="goal"))
        deps = _shared_dependencies(
            calls,
            overrides={
                "perceive": [_perception("grid-1"), _perception("grid-2")],
                "resolve": [_goal(), _goal(), _goal()],
                "plan": [_plan(), _plan(), _plan()],
                "vet": [_vet(False, reason="first veto"), _vet(False, reason="second veto"), _vet(True)],
                "execute": [_execute(grid_hash="grid-2")],
                "evaluate": [_evaluation(WorkflowDecision.TERMINATE, meaningful_progress=True, reason="done")],
            },
        )
        deps.annatar = mock_reason

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=5)).run(WorkflowState(), {"grid": [[1]]})

        assert calls == [
            "perceive", "resolve", "plan", "vet", "resolve", "plan", "vet",
            "perceive", "resolve", "plan", "vet", "execute", "evaluate",
        ]
        assert result.status == WorkflowStatus.TERMINATED
        assert result.reason == "done"
        assert mock_reason.call_count == 1
        assert result.state.annatar_anchor_hint.decision == "repeat_deepen"
        # The second-veto cycle never reached execute/evaluate, so only the
        # one real cycle after it counts toward completed_cycles.
        assert result.completed_cycles == 1

    def test_first_veto_early_exit_also_routes_through_annatar_when_limit_is_zero(self):
        """The OTHER second_veto call site (the early-out fired when
        cycle_vetoes > max_replan_passes_per_cycle, only reachable with a
        0-configured limit) needs the identical treatment -- not just the
        far more common two-veto path."""
        calls: list[str] = []
        mock_reason = MagicMock(return_value=AnnatarOutcome(decision="terminate"))
        deps = _shared_dependencies(calls, overrides={"vet": [_vet(False, reason="first veto")]})
        deps.annatar = mock_reason

        result = WorkflowOrchestrator(
            deps, limits=WorkflowLimits(max_cycles=3, max_replan_passes_per_cycle=0)
        ).run(WorkflowState(), {"grid": [[1]]})

        assert calls == ["perceive", "resolve", "plan", "vet"]
        assert mock_reason.call_count == 1
        assert result.status == WorkflowStatus.TERMINATED
        assert result.reason == "annatar_exhausted"


class TestFirstVetoVisibilityFoldedIntoAnnatarCall:
    """A212 audit (2026-08-25): unlike second_veto (A207, full escalation --
    the episode is about to end without Annatar's say) and unlike
    check_budget (A209, informed-not-empowered -- a hard ceiling Annatar
    cannot override), a FIRST veto resolved by the same-cycle local
    resolve/plan/vet retry is bounded and low-stakes: it doesn't end
    anything, and the retry almost always succeeds. The audit's conclusion
    was "visibility only" -- fold the veto's reason/alternative into the
    very next Annatar invocation (which, in the retry-succeeds case, already
    happens later in the SAME cycle -- no new call, no new branch), without
    giving Annatar any decision authority over the local replan itself.
    These tests pin: (1) the signal actually reaches Annatar, (2) it does
    NOT leak forward into a later cycle that had no veto of its own (state.
    latest_veto_reason is never reset, so a naive implementation reading it
    directly would go stale), and (3) the local replan's own control flow
    -- the exact phase-call sequence -- is unchanged from before this card."""

    def test_first_veto_reason_and_alternative_reach_annatar_when_retry_succeeds(self):
        calls: list[str] = []
        mock_annatar = MagicMock(return_value=AnnatarOutcome(decision="terminate"))
        deps = _shared_dependencies(
            calls,
            overrides={
                "vet": [_vet(False, reason="action-1 attempted 3 times with weak evidence"), _vet(True)],
                "plan": [_plan(), _plan()],
                "resolve": [_goal(), _goal()],
                "evaluate": [_evaluation(WorkflowDecision.CONTINUE, meaningful_progress=False, reason="flat")],
            },
        )
        deps.annatar = mock_annatar

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(WorkflowState(), {"grid": [[1]]})

        assert mock_annatar.call_count == 1
        call = mock_annatar.call_args_list[0]
        assert call.kwargs["veto_reason"] == "action-1 attempted 3 times with weak evidence"
        assert call.kwargs["veto_alternative_action_id"] == "action-2"
        # Local replan's own control flow is byte-for-byte the same shape as
        # before this card: resolve/plan/vet ran a second time within the
        # same cycle (the local retry), then execute/evaluate proceeded
        # normally -- no second-veto routing, no extra cycle, no change to
        # what phases ran or in what order.
        assert calls == ["perceive", "resolve", "plan", "vet", "resolve", "plan", "vet", "execute", "evaluate"]
        assert result.status == WorkflowStatus.TERMINATED
        assert result.reason == "annatar_exhausted"

    def test_no_veto_this_cycle_passes_none_even_with_a_stale_veto_from_an_earlier_cycle(self):
        calls: list[str] = []
        outcomes = deque(
            [
                AnnatarOutcome(decision="repeat_deepen", anchor_ref="g1", anchor_type="goal"),
                AnnatarOutcome(decision="terminate"),
            ]
        )
        mock_annatar = MagicMock(side_effect=lambda *a, **k: outcomes.popleft())
        deps = _shared_dependencies(
            calls,
            overrides={
                "perceive": [_perception("grid-1"), _perception("grid-2")],
                "resolve": [_goal(), _goal(), _goal()],
                "plan": [_plan(), _plan(), _plan()],
                "vet": [_vet(False, reason="first cycle veto"), _vet(True), _vet(True)],
                "execute": [_execute(grid_hash="grid-2"), _execute(grid_hash="grid-3")],
                "evaluate": [
                    _evaluation(WorkflowDecision.CONTINUE, meaningful_progress=False, reason="flat"),
                    _evaluation(WorkflowDecision.CONTINUE, meaningful_progress=False, reason="flat again"),
                ],
            },
        )
        deps.annatar = mock_annatar

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=5)).run(WorkflowState(), {"grid": [[1]]})

        assert mock_annatar.call_count == 2
        first_call, second_call = mock_annatar.call_args_list
        assert first_call.kwargs["veto_reason"] == "first cycle veto"
        assert first_call.kwargs["veto_alternative_action_id"] == "action-2"
        # state.latest_veto_reason is never reset -- cycle 2 had no veto of
        # its own, so this must read None, not the stale value left over
        # from cycle 1.
        assert second_call.kwargs["veto_reason"] is None
        assert second_call.kwargs["veto_alternative_action_id"] is None
        assert result.status == WorkflowStatus.TERMINATED
        assert result.reason == "annatar_exhausted"

    def test_no_annatar_configured_preserves_exact_prior_behavior(self):
        """Regression guard: with no Annatar wired in, a first veto followed
        by a successful retry must behave exactly as it did before this
        card -- this card only ever reads state.latest_veto_reason to build
        a kwarg for an Annatar call that, with annatar=None, never
        happens."""
        calls: list[str] = []
        deps = _shared_dependencies(
            calls,
            overrides={
                "vet": [_vet(False, reason="action-1 attempted 3 times with weak evidence"), _vet(True)],
                "plan": [_plan(), _plan()],
                "resolve": [_goal(), _goal()],
            },
        )

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(WorkflowState(), {"grid": [[1]]})

        assert calls == ["perceive", "resolve", "plan", "vet", "resolve", "plan", "vet", "execute", "evaluate"]
        assert result.status == WorkflowStatus.TERMINATED
        assert result.state.latest_veto_reason == "action-1 attempted 3 times with weak evidence"


# ── Unit tests: agents/arc4/annatar_signals.compute_cycle_signals ──────


def _perception_snapshot(grid_hash: str = "h1") -> PerceptionSnapshot:
    return PerceptionSnapshot(observation={"grid": grid_hash}, grid_hash=grid_hash)


def _execution_result(action_id: str = "a1", candidate: PlanCandidate | None = None) -> ExecutionResult:
    if candidate is None:
        candidate = PlanCandidate(action_id=action_id, goal_id="g1")
    return ExecutionResult(action_id=action_id, candidate=candidate, observation={"grid": "h2"})


def _evaluation_result(*, meaningful_progress: bool, grid_changed: bool) -> EvaluationResult:
    return EvaluationResult(
        decision=WorkflowDecision.CONTINUE,
        meaningful_progress=meaningful_progress,
        metadata={"grid_changed": grid_changed},
    )


class TestComputeCycleSignals:
    def test_graph_port_none_produces_safe_defaults(self):
        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=True),
            anchor_ref="g1",
            anchor_type="goal",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=None,
        )
        assert signals.confidence == 0.0
        assert signals.untested_remaining is True
        assert signals.all_falsified is False
        assert signals.execution_inconclusive is False

    def test_execution_inconclusive_reads_evaluation_metadata_grid_changed(self):
        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=False),
            anchor_ref="g1",
            anchor_type="goal",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=None,
        )
        assert signals.execution_inconclusive is True

    def test_meaningful_progress_avoids_inconclusive_even_if_grid_unchanged(self):
        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=True, grid_changed=False),
            anchor_ref="g1",
            anchor_type="goal",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=None,
        )
        assert signals.execution_inconclusive is False

    def test_entity_anchor_pulls_confidence_from_graph_neighborhood(self):
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {
            "hypotheses": [{"confidence": 0.4, "falsified": False}, {"confidence": 0.9, "falsified": True}],
            "rules": [{"confidence": 0.6, "falsified": False}],
        }
        graph_port.fetch_untested_actions.return_value = ["ACTION1"]

        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=True),
            anchor_ref="e1",
            anchor_type="entity",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=graph_port,
        )
        # Falsified 0.9 hypothesis excluded; live max is the rule at 0.6.
        assert signals.confidence == 0.6
        assert signals.untested_remaining is True
        graph_port.fetch_entity_neighborhood.assert_called_once_with("e1")

    def test_goal_anchor_does_not_query_entity_neighborhood(self):
        graph_port = MagicMock()
        graph_port.fetch_untested_actions.return_value = []

        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=True),
            anchor_ref="g1",
            anchor_type="goal",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=graph_port,
        )
        graph_port.fetch_entity_neighborhood.assert_not_called()
        assert signals.untested_remaining is False

    def test_graph_query_exceptions_degrade_safely(self):
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.side_effect = RuntimeError("graph down")
        graph_port.fetch_untested_actions.side_effect = RuntimeError("graph down")

        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=True),
            anchor_ref="e1",
            anchor_type="entity",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=graph_port,
        )
        assert signals.confidence == 0.0
        assert signals.untested_remaining is True

    def test_stall_reason_forces_all_falsified_and_no_untested_remaining(self):
        graph_port = MagicMock()
        graph_port.fetch_untested_actions.return_value = ["ACTION1", "ACTION2"]

        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=True),
            anchor_ref="g1",
            anchor_type="goal",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=graph_port,
            stall_reason="stall_detected",
        )
        # Without stall folding this would read untested_remaining=True from
        # the graph mock above -- stall_reason must override it.
        assert signals.all_falsified is True
        assert signals.untested_remaining is False

    def test_veto_reason_and_alternative_pass_through_when_provided(self):
        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=True),
            anchor_ref="g1",
            anchor_type="goal",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=None,
            veto_reason="action-1 has been falsified 2 times",
            veto_alternative_action_id="action-2",
        )
        assert signals.veto_reason == "action-1 has been falsified 2 times"
        assert signals.veto_alternative_action_id == "action-2"

    def test_veto_reason_defaults_to_none(self):
        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=True),
            anchor_ref="g1",
            anchor_type="goal",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=None,
        )
        assert signals.veto_reason is None
        assert signals.veto_alternative_action_id is None

    def test_veto_reason_does_not_change_transition_decision(self):
        """A212's core visibility-only constraint: veto_reason/
        veto_alternative_action_id must carry zero decision weight.
        transition() output must be identical whether or not they're set,
        holding every other signal fixed."""
        from agents.arc4.annatar_state_machine import transition

        base_kwargs = dict(
            meaningful_progress=False,
            confidence=0.2,
            untested_remaining=True,
            all_falsified=False,
            execution_inconclusive=False,
            deepening_cycle_count=0,
            already_retried=False,
        )
        without_veto = CycleSignals(**base_kwargs)
        with_veto = CycleSignals(**base_kwargs, veto_reason="some rejection", veto_alternative_action_id="action-9")

        assert transition(InvestigationState.EXPLORING, without_veto) == transition(InvestigationState.EXPLORING, with_veto)

    def test_readiness_report_none_leaves_new_fields_none(self):
        """A230: every existing run_annatar_cycle/compute_cycle_signals
        caller passes no readiness_report -- confirms the new parameter is
        optional and defaults to not affecting anything."""
        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=True),
            anchor_ref="g1",
            anchor_type="goal",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=None,
        )
        assert signals.readiness_status is None
        assert signals.readiness_entities_mapped is None
        assert signals.readiness_entities_total is None

    def test_readiness_report_fields_pass_through_when_provided(self):
        from agents.arc4.annatar_state_machine import ReadinessStatus

        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=True),
            anchor_ref="g1",
            anchor_type="goal",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=None,
            readiness_report={
                "status": ReadinessStatus.NOT_READY,
                "entities_mapped": 1,
                "entities_total": 4,
            },
        )
        assert signals.readiness_status == ReadinessStatus.NOT_READY
        assert signals.readiness_entities_mapped == 1
        assert signals.readiness_entities_total == 4

    def test_resolve_report_none_leaves_new_fields_none(self):
        """A234: every existing run_annatar_cycle/compute_cycle_signals
        caller passes no resolve_report -- confirms the new parameter is
        optional and defaults to not affecting anything."""
        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=True),
            anchor_ref="g1",
            anchor_type="goal",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=None,
        )
        assert signals.resolve_grounding_gate_passed is None
        assert signals.resolve_llm_escalated is None
        assert signals.resolve_hypothesis_ambiguity is None

    def test_resolve_report_fields_pass_through_when_provided(self):
        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=True),
            anchor_ref="g1",
            anchor_type="goal",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=None,
            resolve_report={
                "grounding_gate_passed": False,
                "llm_escalated": True,
                "llm_reason": "ambiguous hypotheses",
                "hypothesis_count": 3,
                "top_two_confidence_gap": 0.05,
            },
        )
        assert signals.resolve_grounding_gate_passed is False
        assert signals.resolve_llm_escalated is True
        assert signals.resolve_hypothesis_ambiguity == 0.05

    def test_resolve_report_does_not_change_transition_decision(self):
        """A234's Track A conclusion: resolve_grounding_gate_passed/
        resolve_llm_escalated/resolve_hypothesis_ambiguity must carry zero
        decision weight, same as veto_reason (A212) and readiness_status
        (A230) before it -- transition() output must be identical whether
        or not they're set, holding every other signal fixed."""
        from agents.arc4.annatar_state_machine import transition

        base_kwargs = dict(
            meaningful_progress=False,
            confidence=0.2,
            untested_remaining=True,
            all_falsified=False,
            execution_inconclusive=False,
            deepening_cycle_count=0,
            already_retried=False,
        )
        without_resolve_report = CycleSignals(**base_kwargs)
        with_resolve_report = CycleSignals(
            **base_kwargs,
            resolve_grounding_gate_passed=False,
            resolve_llm_escalated=True,
            resolve_hypothesis_ambiguity=0.01,
        )

        assert transition(InvestigationState.EXPLORING, without_resolve_report) == transition(
            InvestigationState.EXPLORING, with_resolve_report
        )


class TestResolveReportVisibilityFoldedIntoAnnatarCall:
    """A234: goal_resolver.py::resolve()'s own already-computed per-cycle
    output (grounding_gate_passed/llm_escalated/llm_reason/hypothesis
    ambiguity) previously never reached Annatar's normal-cycle call at all
    -- the same structural gap A230 found and fixed for the readiness/probe
    phase, now closed here for goal resolution. This test pins that the
    signal actually reaches the existing self._dependencies.annatar(...)
    call (no new call site), built from the ResolvedGoal object workflow.py
    already holds as resolved_goal_payload."""

    def test_resolve_report_reaches_annatar_normal_cycle_call(self):
        calls: list[str] = []
        mock_annatar = MagicMock(return_value=AnnatarOutcome(decision="terminate"))
        goal = ResolvedGoal(
            selected=GoalHypothesis(goal_id="goal-1", description="goal-1", confidence=0.5),
            alternatives=(GoalHypothesis(goal_id="goal-2", description="goal-2", confidence=0.42),),
            grounding_gate_passed=False,
            metadata={
                "hypotheses": [{"goal_id": "goal-1"}, {"goal_id": "goal-2"}],
                "llm_escalated": True,
                "llm_reason": "ambiguous hypotheses",
            },
        )
        deps = _shared_dependencies(
            calls,
            overrides={
                "resolve": [PhaseResult(phase=WorkflowPhase.RESOLVE, payload=goal)],
                "evaluate": [_evaluation(WorkflowDecision.CONTINUE, meaningful_progress=False, reason="flat")],
            },
        )
        deps.annatar = mock_annatar

        WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(WorkflowState(), {"grid": [[1]]})

        assert mock_annatar.call_count == 1
        resolve_report = mock_annatar.call_args_list[0].kwargs["resolve_report"]
        assert resolve_report["grounding_gate_passed"] is False
        assert resolve_report["llm_escalated"] is True
        assert resolve_report["llm_reason"] == "ambiguous hypotheses"
        assert resolve_report["hypothesis_count"] == 2
        assert resolve_report["top_two_confidence_gap"] == pytest.approx(0.08)

    def test_no_alternatives_reports_none_confidence_gap(self):
        calls: list[str] = []
        mock_annatar = MagicMock(return_value=AnnatarOutcome(decision="terminate"))
        goal = ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="goal-1", confidence=0.5))
        deps = _shared_dependencies(
            calls,
            overrides={
                "resolve": [PhaseResult(phase=WorkflowPhase.RESOLVE, payload=goal)],
                "evaluate": [_evaluation(WorkflowDecision.CONTINUE, meaningful_progress=False, reason="flat")],
            },
        )
        deps.annatar = mock_annatar

        WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(WorkflowState(), {"grid": [[1]]})

        resolve_report = mock_annatar.call_args_list[0].kwargs["resolve_report"]
        assert resolve_report["top_two_confidence_gap"] is None
        assert resolve_report["grounding_gate_passed"] is True
        assert resolve_report["llm_escalated"] is False

    def test_no_annatar_configured_preserves_exact_prior_behavior(self):
        """Regression guard: with no Annatar wired in, resolve()'s output is
        never read into a resolve_report at all -- this card only ever
        builds the dict for an Annatar call that, with annatar=None, never
        happens."""
        calls: list[str] = []
        deps = _shared_dependencies(calls)

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(WorkflowState(), {"grid": [[1]]})

        assert calls == ["perceive", "resolve", "plan", "vet", "execute", "evaluate"]
        assert result.status == WorkflowStatus.TERMINATED


# ── Unit tests: agents/arc4/annatar_signals.run_annatar_cycle ─────────


class TestRunAnnatarCycleAnchorSelection:
    def test_probe_cycle_prefers_entity_ref_from_executed_candidate(self):
        """A246: entity-preferring fresh-anchor creation is probe-phase
        behavior -- readiness_report is not None here, matching A230's
        convention. Renamed from test_fresh_attempt_prefers_entity_ref_
        from_executed_candidate: that name/assertion described what used to
        happen on EVERY fresh attempt regardless of phase (the bug); it now
        also needs an explicit readiness_report to represent probing. See
        test_goal_directed_cycle_prefers_active_goal_over_entity_ref below
        for the corrected goal-directed behavior, and backlog/A246.md."""
        from agents.arc4.annatar_state_machine import ReadinessStatus

        candidate = PlanCandidate(action_id="ACTION6", goal_id="g1", metadata={"entity_ref": "e42"})
        state = WorkflowState(active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g1", description="d")))
        execution = _execution_result(action_id="ACTION6", candidate=candidate)
        evaluation = _evaluation_result(meaningful_progress=False, grid_changed=True)

        outcome = run_annatar_cycle(
            state,
            _perception_snapshot(),
            execution,
            evaluation,
            graph_port=None,
            readiness_report={"status": ReadinessStatus.NOT_READY, "entities_mapped": 0, "entities_total": 1},
        )

        assert outcome.anchor_type == "entity"
        assert outcome.anchor_ref == "e42"

    def test_goal_directed_cycle_prefers_active_goal_over_entity_ref(self):
        """A246: during goal-directed play (readiness_report is None, the
        default every non-probe call site actually uses), the just-executed
        candidate's entity_ref must NOT override the active goal -- this is
        the exact SK48 live bug this card fixes (a concluding goal-type
        anchor's very next anchor incidentally becoming entity-type because
        the last action happened to be an ACTION6 click). See
        backlog/A246.md and tests/test_a246_anchor_selection_readiness_
        context.py for the dedicated coverage."""
        candidate = PlanCandidate(action_id="ACTION6", goal_id="g1", metadata={"entity_ref": "e42"})
        state = WorkflowState(active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g1", description="d")))
        execution = _execution_result(action_id="ACTION6", candidate=candidate)
        evaluation = _evaluation_result(meaningful_progress=False, grid_changed=True)

        outcome = run_annatar_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=None)

        assert outcome.anchor_type == "goal"
        assert outcome.anchor_ref == "g1"

    def test_fresh_attempt_falls_back_to_active_goal_id(self):
        candidate = PlanCandidate(action_id="a1", goal_id="g1")
        state = WorkflowState(active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g7", description="d")))
        execution = _execution_result(action_id="a1", candidate=candidate)
        evaluation = _evaluation_result(meaningful_progress=False, grid_changed=True)

        outcome = run_annatar_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=None)

        assert outcome.anchor_type == "goal"
        assert outcome.anchor_ref == "g7"

    def test_advance_clears_active_investigation_anchor(self):
        candidate = PlanCandidate(action_id="a1", goal_id="g1")
        state = WorkflowState(active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g7", description="d")))
        execution = _execution_result(action_id="a1", candidate=candidate)
        # meaningful_progress True -> SATISFIED -> ADVANCE
        evaluation = _evaluation_result(meaningful_progress=True, grid_changed=True)

        outcome = run_annatar_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=None)

        assert outcome.decision == "advance"
        assert state.active_investigation_anchor is None


class TestRunAnnatarCycleWholeEpisodeFutility:
    """User-directed follow-up (2026-08-25) after live-smoke evidence showed
    a real gap: a 60-step run cycled through 4+ different goal anchors, all
    of them completely unproductive (meaningful_progress=False on every one
    of 120 evaluate snapshots, zero grid changes across 60 real ARC API
    actions) -- and nothing in Annatar noticed the pattern across
    anchors. The per-anchor state machine correctly recognizes "this one
    anchor is exhausted" and advances to a fresh anchor, but nothing
    aggregated "I've now tried N different anchors and every single one
    struck out" into a real whole-episode decision, so the run just burned
    its full wall-clock budget. This is exactly the gap A200's own design
    note anticipated ("Whole-episode TERMINATE is decided by the
    integration layer [A202]... since it alone has visibility into 'is
    there anything left to advance to at all'") but which A202's actual
    implementation never built -- decision_for_state() never produces
    "terminate", so state.annatar_unproductive_anchor_streak +
    run_annatar_cycle's own override are what actually closes this gap.
    """

    def _unproductive_advance(self, state, stall_reason="stalled"):
        """One cycle that concludes an anchor via EXHAUSTED->ADVANCE without
        ever registering meaningful_progress (grid_changed=True keeps
        execution_inconclusive False so RETRY doesn't intercept first).

        A221 Finding 1: this used to reach EXHAUSTED via stall_reason folding
        into all_falsified=True/untested_remaining=False -- that path was
        removed (all_falsified was never graph-derived; see
        annatar_state_machine.py::transition()'s updated comment). Reaching
        EXHAUSTED now requires a graph-grounded CynefinDomain.CHAOTIC anchor
        instead: anchor_type must resolve to "entity" (the only type
        compute_cycle_signals computes domain for) and graph_port returns
        all-falsified rule evidence. stall_reason is still passed and still
        folds into all_falsified for realism (matching a genuinely stalled
        episode) but no longer does any work toward reaching EXHAUSTED
        itself -- CHAOTIC does that now.

        A246: this class exercises anchor CONCLUSION/streak-counting, not
        anchor CREATION -- unrelated to this card's fix. Before A246, an
        entity_ref-carrying candidate was enough to make a *fresh* anchor
        entity-type; after A246, a goal-directed cycle (readiness_report is
        None, the same default every call here already used) with
        state.active_goal set now prefers the active goal instead. Since
        these tests need an entity-type anchor specifically (for the
        CHAOTIC domain classification above) and are not testing which
        anchor type gets freshly created, inject a pre-existing entity-type
        anchor directly whenever one isn't already active -- bypassing the
        creation block entirely, exactly like
        test_progress_partway_through_a_deepening_anchor_counts_as_productive
        already does a few tests below. This keeps every assertion in this
        class about streak counting byte-for-byte unchanged."""
        if state.active_investigation_anchor is None:
            state.active_investigation_anchor = {
                "anchor_ref": "e1",
                "anchor_type": "entity",
                "thread_id": None,
                "state": InvestigationState.EXPLORING.value,
                "deepening_cycle_count": 0,
                "already_retried": False,
                "any_progress": False,
                "edge_writes_at_start": state.world_model_edge_writes,
            }
        candidate = PlanCandidate(action_id="a1", goal_id="g1", metadata={"entity_ref": "e1"})
        execution = _execution_result(action_id="a1", candidate=candidate)
        evaluation = _evaluation_result(meaningful_progress=False, grid_changed=True)
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {
            "hypotheses": [],
            "rules": [{"confidence": 0.0, "falsified": True, "to_color": 5}],
        }
        graph_port.fetch_untested_actions.return_value = []
        return run_annatar_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=graph_port, stall_reason=stall_reason)

    def _productive_advance(self, state):
        """One cycle that concludes an anchor via SATISFIED->ADVANCE with
        real meaningful_progress registered."""
        candidate = PlanCandidate(action_id="a1", goal_id="g1")
        execution = _execution_result(action_id="a1", candidate=candidate)
        evaluation = _evaluation_result(meaningful_progress=True, grid_changed=True)
        return run_annatar_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=None)

    def test_single_unproductive_anchor_increments_streak_without_terminating(self):
        state = WorkflowState(active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g7", description="d")))
        outcome = self._unproductive_advance(state)

        assert outcome.decision == "advance"
        assert state.annatar_unproductive_anchor_streak == 1

    def test_default_threshold_terminates_after_three_consecutive_unproductive_anchors(self):
        state = WorkflowState(active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g7", description="d")))

        outcome1 = self._unproductive_advance(state)
        outcome2 = self._unproductive_advance(state)
        outcome3 = self._unproductive_advance(state)

        assert outcome1.decision == "advance"
        assert outcome2.decision == "advance"
        assert outcome3.decision == "terminate"
        assert state.annatar_unproductive_anchor_streak == 3
        assert state.active_investigation_anchor is None

    def test_a_productive_anchor_resets_the_streak(self):
        state = WorkflowState(active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g7", description="d")))

        self._unproductive_advance(state)
        self._unproductive_advance(state)
        assert state.annatar_unproductive_anchor_streak == 2

        productive = self._productive_advance(state)
        assert productive.decision == "advance"
        assert state.annatar_unproductive_anchor_streak == 0

        # Confirms it's a genuine reset, not just "doesn't increment": two
        # more unproductive anchors after the reset must NOT terminate,
        # since the streak restarted from 0.
        outcome = self._unproductive_advance(state)
        assert outcome.decision == "advance"
        assert state.annatar_unproductive_anchor_streak == 1

    def test_progress_partway_through_a_deepening_anchor_counts_as_productive(self):
        """An anchor that shows meaningful_progress on an earlier cycle but
        then concludes unproductively on a LATER cycle must still count as
        productive overall -- any_progress is tracked across the anchor's
        whole life, not just its final cycle."""
        state = WorkflowState(
            active_investigation_anchor={
                "anchor_ref": "e1",
                # A221 Finding 1: must be "entity", not "goal" -- domain
                # (and therefore CHAOTIC->EXHAUSTED) is only ever computed
                # for entity-type anchors (compute_cycle_signals gates on
                # anchor_type == "entity"). A pre-existing anchor skips the
                # anchor-init block entirely, so this can't be fixed up by
                # _unproductive_advance's candidate metadata -- it has to be
                # set here directly.
                "anchor_type": "entity",
                "thread_id": None,
                "state": InvestigationState.DEEPENING.value,
                "deepening_cycle_count": 0,
                "already_retried": False,
                "any_progress": True,  # this anchor registered progress on an earlier cycle
            }
        )
        outcome = self._unproductive_advance(state)

        assert outcome.decision == "advance"
        assert state.annatar_unproductive_anchor_streak == 0

    def test_graph_growth_between_cycles_of_the_same_anchor_counts_as_productive(self):
        """A235: real graph growth (state.world_model_edge_writes increasing
        since the anchor's own edge_writes_at_start snapshot) attributable
        to THIS anchor's own investigation must count as progress too, even
        when signals.meaningful_progress -- a narrow whole-puzzle-progress
        boolean -- never fires. Confirmed live: a 5-step episode terminated
        immediately after real, graph-confirmed CHAOTIC/COMPLEX/CONVERGED
        classifications were written to the graph during that exact
        episode, because meaningful_progress alone never credited any of it
        (see backlog/A235.md). This simulates cycle 2+ of the same anchor
        (edge_writes_at_start was snapshotted on an earlier cycle, then a
        later cycle's own evaluate wrote a confirmed graph edge) -- the one
        case this fix is designed to catch, per Track A's timing analysis
        (a brand-new anchor's very first cycle can't self-credit its own
        same-cycle write; see the code comment in annatar_signals.py)."""
        state = WorkflowState(
            active_investigation_anchor={
                "anchor_ref": "e1",
                "anchor_type": "entity",
                "thread_id": None,
                "state": InvestigationState.DEEPENING.value,
                "deepening_cycle_count": 0,
                "already_retried": False,
                "any_progress": False,
                "edge_writes_at_start": 0,
            }
        )
        # Simulates real graph growth having happened on an earlier cycle
        # of this SAME anchor's own investigation, since its
        # edge_writes_at_start snapshot was taken.
        state.world_model_edge_writes = 2

        outcome = self._unproductive_advance(state)

        assert outcome.decision == "advance"
        # meaningful_progress was False the whole time (per
        # _unproductive_advance) -- only graph_grew credits this anchor.
        assert state.annatar_unproductive_anchor_streak == 0

    def test_flat_edge_writes_across_cycles_still_counts_as_unproductive(self):
        """Regression: the guard's original purpose must survive this
        fix. An anchor whose meaningful_progress stays False AND whose
        state.world_model_edge_writes is genuinely flat (no growth at all,
        matching edge_writes_at_start on every cycle) must still correctly
        count as unproductive -- 3 such anchors in a row must still
        terminate the whole episode, exactly as before this card."""
        state = WorkflowState(active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g7", description="d")))
        # world_model_edge_writes stays at its default (0) for the whole
        # test -- _unproductive_advance never touches it, so every anchor's
        # edge_writes_at_start snapshot (taken at 0) never grows.
        assert state.world_model_edge_writes == 0

        outcome1 = self._unproductive_advance(state)
        outcome2 = self._unproductive_advance(state)
        outcome3 = self._unproductive_advance(state)

        assert outcome1.decision == "advance"
        assert outcome2.decision == "advance"
        assert outcome3.decision == "terminate"
        assert state.annatar_unproductive_anchor_streak == 3
        assert state.active_investigation_anchor is None

    def test_max_unproductive_anchors_kwarg_overrides_default_threshold(self):
        state = WorkflowState(
            active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g7", description="d")),
            # A246: inject a pre-existing entity-type anchor directly (same
            # reasoning as _unproductive_advance's own A246 comment) so this
            # stays a CHAOTIC-domain-entity scenario rather than the new
            # goal-preferring fresh-anchor-creation path picking "goal".
            active_investigation_anchor={
                "anchor_ref": "e1",
                "anchor_type": "entity",
                "thread_id": None,
                "state": InvestigationState.EXPLORING.value,
                "deepening_cycle_count": 0,
                "already_retried": False,
                "any_progress": False,
                "edge_writes_at_start": 0,
            },
        )
        # A221 Finding 1: entity_ref candidate + CHAOTIC-shaped graph_port,
        # same reasoning as _unproductive_advance -- reaching EXHAUSTED no
        # longer works via all_falsified/stall_reason alone.
        candidate = PlanCandidate(action_id="a1", goal_id="g1", metadata={"entity_ref": "e1"})
        execution = _execution_result(action_id="a1", candidate=candidate)
        evaluation = _evaluation_result(meaningful_progress=False, grid_changed=True)
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {
            "hypotheses": [],
            "rules": [{"confidence": 0.0, "falsified": True, "to_color": 5}],
        }
        graph_port.fetch_untested_actions.return_value = []

        outcome1 = run_annatar_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=graph_port, stall_reason="stalled", max_unproductive_anchors=1)

        assert outcome1.decision == "terminate"
        assert state.annatar_unproductive_anchor_streak == 1

    def test_terminate_outcome_reports_no_anchor_ref(self):
        """A whole-episode terminate isn't "stay anchored on X" -- unlike
        repeat_deepen/repeat_retry, it must not report an anchor_ref/type,
        matching how "advance" already reports None for both."""
        state = WorkflowState(active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g7", description="d")))
        for _ in range(3):
            outcome = self._unproductive_advance(state)

        assert outcome.decision == "terminate"
        assert outcome.anchor_ref is None
        assert outcome.anchor_type is None


class TestRunAnnatarCycleDeepeningEscalatesToAwaitingLLMWithinOneCycle:
    """Regression test for a live-smoke-discovered crash (2026-08-25): when
    transition() itself produces InvestigationState.AWAITING_LLM as the new
    state within the *same* cycle (a DEEPENING thread whose
    deepening_cycle_count has just reached AnnatarLimits.
    max_deepening_cycles_before_llm), run_annatar_cycle passed that
    new_state straight to decision_for_state(), which explicitly raises
    ValueError for AWAITING_LLM (per annatar_state_machine.py's own
    docstring: it must be resolved via apply_llm_vote() first, never handed
    to decision_for_state() directly). Every existing AWAITING_LLM test
    (above, and in test_a205_annatar_error_handling.py) starts with the
    anchor already parked in AWAITING_LLM state -- none exercised the
    transition-into-AWAITING_LLM-this-cycle path, so this genuine
    integration-seam bug reached a real live run (crashed after 4 real
    ARC API steps: 'ValueError: no decision mapping for state:
    awaiting_llm') before being caught here.
    """

    def test_transition_into_awaiting_llm_does_not_crash_and_repeats_instead_of_deciding(self):
        state = WorkflowState(
            active_investigation_anchor={
                "anchor_ref": "g1",
                "anchor_type": "goal",
                "thread_id": None,
                "state": InvestigationState.DEEPENING.value,
                # AnnatarLimits.max_deepening_cycles_before_llm defaults to
                # 3 -- this is the exact cycle where transition() escalates
                # DEEPENING -> AWAITING_LLM.
                "deepening_cycle_count": 3,
                "already_retried": False,
            }
        )
        candidate = PlanCandidate(action_id="a1", goal_id="g1")
        execution = _execution_result(action_id="a1", candidate=candidate)
        # grid_changed=True keeps execution_inconclusive False (a RETRY
        # would otherwise fire first and never reach the deepening-limit
        # check) while meaningful_progress stays False and confidence stays
        # 0.0 (below the 0.75 SATISFIED threshold) so SATISFIED doesn't fire
        # either; untested_remaining defaults True and all_falsified
        # defaults False (graph_port=None) so EXHAUSTED doesn't fire -- the
        # only remaining branch is the deepening-limit -> AWAITING_LLM one.
        evaluation = _evaluation_result(meaningful_progress=False, grid_changed=True)

        outcome = run_annatar_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=None)

        # Must not raise. Must not silently be treated as "advance" (that
        # would discard the thread mid-escalation). The only correct
        # decision here is to repeat -- the *next* cycle will see
        # current_state == AWAITING_LLM and actually resolve it via
        # resolve_llm_vote/apply_llm_vote.
        assert outcome.decision == "repeat_deepen"
        assert state.active_investigation_anchor is not None
        assert state.active_investigation_anchor["state"] == InvestigationState.AWAITING_LLM.value

    def test_next_cycle_resolves_the_parked_awaiting_llm_state_correctly(self):
        """Companion to the test above: once AWAITING_LLM is correctly
        parked on the anchor, the *following* cycle must resolve it via
        resolve_llm_vote/apply_llm_vote exactly like the pre-existing
        TestRunAnnatarCycleAwaitingLLM coverage below -- proving the two
        cycles compose correctly end-to-end, not just each in isolation."""
        state = WorkflowState(
            active_investigation_anchor={
                "anchor_ref": "g1",
                "anchor_type": "goal",
                "thread_id": None,
                "state": InvestigationState.AWAITING_LLM.value,
                "deepening_cycle_count": 3,
                "already_retried": False,
            }
        )
        candidate = PlanCandidate(action_id="a1", goal_id="g1")
        execution = _execution_result(action_id="a1", candidate=candidate)
        evaluation = _evaluation_result(meaningful_progress=False, grid_changed=False)

        with patch.object(annatar_signals_module, "resolve_llm_vote", return_value=InvestigationState.DEEPENING):
            outcome = run_annatar_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=None)

        assert outcome.decision == "repeat_deepen"
        assert state.active_investigation_anchor["state"] == InvestigationState.DEEPENING.value


class TestRunAnnatarCycleAwaitingLLM:
    def test_awaiting_llm_calls_resolve_llm_vote_and_flows_through_apply_llm_vote(self):
        state = WorkflowState(
            active_investigation_anchor={
                "anchor_ref": "g1",
                "anchor_type": "goal",
                "thread_id": None,
                "state": InvestigationState.AWAITING_LLM.value,
                "deepening_cycle_count": 3,
                "already_retried": False,
            }
        )
        candidate = PlanCandidate(action_id="a1", goal_id="g1")
        execution = _execution_result(action_id="a1", candidate=candidate)
        evaluation = _evaluation_result(meaningful_progress=False, grid_changed=True)

        with patch.object(annatar_signals_module, "resolve_llm_vote", return_value=InvestigationState.SATISFIED) as mock_vote:
            outcome = run_annatar_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=None)

        mock_vote.assert_called_once()
        assert outcome.decision == "advance"

    def test_resolve_llm_vote_no_port_returns_exploring_sentinel(self):
        # A202 shipped resolve_llm_vote as a loud NotImplementedError
        # placeholder specifically for A205 to fill in (see this module's
        # own docstring above). A205 replaced it with the real bounded LLM
        # call; a missing llm_port is now one of its handled failure paths,
        # resolving to InvestigationState.EXPLORING (guaranteed outside
        # permissible_llm_transitions -- see tests/test_a205_annatar_error_
        # handling.py for the full failure-mode suite), not an exception.
        signals = CycleSignals(
            meaningful_progress=False,
            confidence=0.0,
            untested_remaining=True,
            all_falsified=False,
            execution_inconclusive=False,
            deepening_cycle_count=0,
            already_retried=False,
        )
        assert resolve_llm_vote(None, WorkflowState(), signals) == InvestigationState.EXPLORING


class TestRunAnnatarCycleExplorationComplete:
    """A230: run_annatar_cycle's own glue code computes AnnatarOutcome.
    exploration_complete directly from the readiness_report's status --
    True for READY/PARTIAL_FALLTHROUGH, False for NOT_READY, None when no
    report was passed this cycle. This is independent of whatever per-anchor
    decision (advance/repeat_deepen/repeat_retry/terminate) the probed
    entity's own domain/progress produces."""

    def test_no_readiness_report_returns_none(self):
        candidate = PlanCandidate(action_id="a1", goal_id="g1")
        state = WorkflowState(active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g7", description="d")))
        execution = _execution_result(action_id="a1", candidate=candidate)
        evaluation = _evaluation_result(meaningful_progress=False, grid_changed=True)

        outcome = run_annatar_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=None)

        assert outcome.exploration_complete is None

    def test_not_ready_report_returns_false(self):
        from agents.arc4.annatar_state_machine import ReadinessStatus

        candidate = PlanCandidate(action_id="a1", goal_id="g1", metadata={"entity_ref": "e1"})
        state = WorkflowState(active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g1", description="d")))
        execution = _execution_result(action_id="a1", candidate=candidate)
        evaluation = _evaluation_result(meaningful_progress=False, grid_changed=True)

        outcome = run_annatar_cycle(
            state,
            _perception_snapshot(),
            execution,
            evaluation,
            graph_port=None,
            readiness_report={"status": ReadinessStatus.NOT_READY, "entities_mapped": 1, "entities_total": 3},
        )

        assert outcome.exploration_complete is False

    def test_ready_report_returns_true(self):
        from agents.arc4.annatar_state_machine import ReadinessStatus

        candidate = PlanCandidate(action_id="a1", goal_id="g1", metadata={"entity_ref": "e1"})
        state = WorkflowState(active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g1", description="d")))
        execution = _execution_result(action_id="a1", candidate=candidate)
        evaluation = _evaluation_result(meaningful_progress=False, grid_changed=True)

        outcome = run_annatar_cycle(
            state,
            _perception_snapshot(),
            execution,
            evaluation,
            graph_port=None,
            readiness_report={"status": ReadinessStatus.READY, "entities_mapped": 3, "entities_total": 3},
        )

        assert outcome.exploration_complete is True

    def test_partial_fallthrough_report_returns_true(self):
        from agents.arc4.annatar_state_machine import ReadinessStatus

        candidate = PlanCandidate(action_id="a1", goal_id="g1", metadata={"entity_ref": "e1"})
        state = WorkflowState(active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g1", description="d")))
        execution = _execution_result(action_id="a1", candidate=candidate)
        evaluation = _evaluation_result(meaningful_progress=False, grid_changed=True)

        outcome = run_annatar_cycle(
            state,
            _perception_snapshot(),
            execution,
            evaluation,
            graph_port=None,
            readiness_report={
                "status": ReadinessStatus.PARTIAL_FALLTHROUGH,
                "entities_mapped": 1,
                "entities_total": 3,
            },
        )

        assert outcome.exploration_complete is True

    def test_exploration_complete_independent_of_per_anchor_decision(self):
        """A NOT_READY readiness report (exploration_complete=False) can
        still coexist with whatever per-anchor decision the probed entity's
        own signals produce (here: meaningful_progress=True -> SATISFIED ->
        advance) -- the two are genuinely independent questions."""
        from agents.arc4.annatar_state_machine import ReadinessStatus

        candidate = PlanCandidate(action_id="a1", goal_id="g1", metadata={"entity_ref": "e1"})
        state = WorkflowState(active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g1", description="d")))
        execution = _execution_result(action_id="a1", candidate=candidate)
        evaluation = _evaluation_result(meaningful_progress=True, grid_changed=True)

        outcome = run_annatar_cycle(
            state,
            _perception_snapshot(),
            execution,
            evaluation,
            graph_port=None,
            readiness_report={"status": ReadinessStatus.NOT_READY, "entities_mapped": 1, "entities_total": 3},
        )

        assert outcome.decision == "advance"
        assert outcome.exploration_complete is False
