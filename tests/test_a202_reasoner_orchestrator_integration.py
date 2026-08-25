"""A202: wires A200's pure state machine + A201's graph client into
WorkflowOrchestrator.run() via the new `reason` dependency.

Test groups:
  - Backward-compat byte-for-byte regression (reason=None) against a real
    pre-A202 baseline of workflow.py, loaded from a copy of the file taken
    immediately before this card's edits.
  - Orchestrator control-flow tests: terminate / repeat_deepen / advance /
    stall-folded-into-reasoner / termination-short-circuits-before-reasoner /
    check_budget unaffected.
  - Unit tests for agents/arc4/reasoner_signals.py's compute_cycle_signals
    and run_reasoner_cycle (including the AWAITING_LLM -> resolve_llm_vote
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

from agents.arc4 import reasoner_signals as reasoner_signals_module
from agents.arc4.investigation_reasoner import CycleSignals, InvestigationState
from agents.arc4.ports import WorkflowDependencies
from agents.arc4.reasoner_signals import compute_cycle_signals, resolve_llm_vote, run_reasoner_cycle
from agents.arc4.types import (
    EvaluationResult,
    ExecutionResult,
    GoalHypothesis,
    PerceptionSnapshot,
    PhaseResult,
    PhaseStatus,
    PlanCandidate,
    PlanningResult,
    ReasonerOutcome,
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

_BASELINE_PATH = (
    Path("/private/tmp/claude-501/-Users-djshelton-Desktop-GitProjects-ARC-AGI")
    / "663f0b9e-9c2a-44e1-85c8-4c23aa63bc65"
    / "scratchpad"
    / "workflow_pre_a202_baseline.py"
)


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


class TestReasonerControlFlow:
    def test_terminate_decision_ends_run_as_reasoner_exhausted(self):
        calls: list[str] = []
        mock_reason = MagicMock(return_value=ReasonerOutcome(decision="terminate"))
        deps = _shared_dependencies(
            calls,
            overrides={"evaluate": [_evaluation(WorkflowDecision.CONTINUE, meaningful_progress=False, reason="flat")]},
        )
        deps.reason = mock_reason

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(WorkflowState(), {"grid": [[1]]})

        assert result.status == WorkflowStatus.TERMINATED
        assert result.reason == "reasoner_exhausted"
        assert mock_reason.call_count == 1

    def test_repeat_deepen_sets_anchor_hint_and_continues(self):
        calls: list[str] = []
        outcomes = deque(
            [
                ReasonerOutcome(decision="repeat_deepen", anchor_ref="g1", anchor_type="goal"),
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
        deps.reason = mock_reason

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=5)).run(WorkflowState(), {"grid": [[1]]})

        assert result.status == WorkflowStatus.TERMINATED
        assert result.completed_cycles == 2
        assert mock_reason.call_count == 1
        assert result.state.reasoner_anchor_hint is not None
        assert result.state.reasoner_anchor_hint.decision == "repeat_deepen"

    def test_advance_decision_clears_anchor_hint(self):
        calls: list[str] = []
        mock_reason = MagicMock(return_value=ReasonerOutcome(decision="advance"))
        deps = _shared_dependencies(
            calls,
            overrides={"evaluate": [_evaluation(WorkflowDecision.CONTINUE, meaningful_progress=False, reason="flat")]},
        )
        deps.reason = mock_reason

        state = WorkflowState(reasoner_anchor_hint=ReasonerOutcome(decision="repeat_deepen"))
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
        deps.reason = mock_reason

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=5)).run(state, {"grid": [[1]]})

        assert result.completed_cycles == 2
        assert result.state.reasoner_anchor_hint is None

    def test_stall_signal_folds_into_reasoner_instead_of_independently_stalling(self):
        calls: list[str] = []
        outcomes = deque(
            [
                ReasonerOutcome(decision="repeat_deepen"),
                ReasonerOutcome(decision="terminate"),
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
        deps.reason = mock_reason

        result = WorkflowOrchestrator(
            deps, limits=WorkflowLimits(max_cycles=5, max_consecutive_no_progress=2)
        ).run(WorkflowState(), {"grid": [[1]]})

        # Old standalone path would have returned STALLED here (see
        # test_stall_terminates_after_repeated_no_progress in
        # test_arc4_workflow.py for the same scenario without a reasoner).
        # With a reasoner configured, the run must NOT end via that path.
        assert result.status != WorkflowStatus.STALLED
        assert mock_reason.call_count == 2
        first_call_kwargs = mock_reason.call_args_list[0].kwargs
        second_call_kwargs = mock_reason.call_args_list[1].kwargs
        assert first_call_kwargs["stall_reason"] is None
        assert second_call_kwargs["stall_reason"] == "stall_detected"
        assert result.status == WorkflowStatus.TERMINATED
        assert result.reason == "reasoner_exhausted"

    def test_evaluation_termination_short_circuits_before_reasoner_runs(self):
        calls: list[str] = []
        mock_reason = MagicMock(return_value=ReasonerOutcome(decision="advance"))
        deps = _shared_dependencies(
            calls,
            overrides={"evaluate": [_evaluation(WorkflowDecision.TERMINATE, meaningful_progress=True, reason="done")]},
        )
        deps.reason = mock_reason

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(WorkflowState(), {"grid": [[1]]})

        assert result.status == WorkflowStatus.TERMINATED
        assert result.reason == "done"
        assert mock_reason.call_count == 0

    def test_check_budget_still_gates_before_anything_else(self):
        calls: list[str] = []
        mock_reason = MagicMock(return_value=ReasonerOutcome(decision="advance"))
        deps = _shared_dependencies(calls)
        deps.reason = mock_reason

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=0)).run(WorkflowState(), {"grid": [[1]]})

        assert calls == []
        assert result.status == WorkflowStatus.BUDGET_EXHAUSTED
        assert mock_reason.call_count == 0


# ── Unit tests: agents/arc4/reasoner_signals.compute_cycle_signals ──────


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


# ── Unit tests: agents/arc4/reasoner_signals.run_reasoner_cycle ─────────


class TestRunReasonerCycleAnchorSelection:
    def test_fresh_attempt_prefers_entity_ref_from_executed_candidate(self):
        candidate = PlanCandidate(action_id="ACTION6", goal_id="g1", metadata={"entity_ref": "e42"})
        state = WorkflowState(active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g1", description="d")))
        execution = _execution_result(action_id="ACTION6", candidate=candidate)
        evaluation = _evaluation_result(meaningful_progress=False, grid_changed=True)

        outcome = run_reasoner_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=None)

        assert outcome.anchor_type == "entity"
        assert outcome.anchor_ref == "e42"

    def test_fresh_attempt_falls_back_to_active_goal_id(self):
        candidate = PlanCandidate(action_id="a1", goal_id="g1")
        state = WorkflowState(active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g7", description="d")))
        execution = _execution_result(action_id="a1", candidate=candidate)
        evaluation = _evaluation_result(meaningful_progress=False, grid_changed=True)

        outcome = run_reasoner_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=None)

        assert outcome.anchor_type == "goal"
        assert outcome.anchor_ref == "g7"

    def test_advance_clears_active_investigation_anchor(self):
        candidate = PlanCandidate(action_id="a1", goal_id="g1")
        state = WorkflowState(active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g7", description="d")))
        execution = _execution_result(action_id="a1", candidate=candidate)
        # meaningful_progress True -> SATISFIED -> ADVANCE
        evaluation = _evaluation_result(meaningful_progress=True, grid_changed=True)

        outcome = run_reasoner_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=None)

        assert outcome.decision == "advance"
        assert state.active_investigation_anchor is None


class TestRunReasonerCycleDeepeningEscalatesToAwaitingLLMWithinOneCycle:
    """Regression test for a live-smoke-discovered crash (2026-08-25): when
    transition() itself produces InvestigationState.AWAITING_LLM as the new
    state within the *same* cycle (a DEEPENING thread whose
    deepening_cycle_count has just reached ReasonerLimits.
    max_deepening_cycles_before_llm), run_reasoner_cycle passed that
    new_state straight to decision_for_state(), which explicitly raises
    ValueError for AWAITING_LLM (per investigation_reasoner.py's own
    docstring: it must be resolved via apply_llm_vote() first, never handed
    to decision_for_state() directly). Every existing AWAITING_LLM test
    (above, and in test_a205_reasoner_error_handling.py) starts with the
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
                # ReasonerLimits.max_deepening_cycles_before_llm defaults to
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

        outcome = run_reasoner_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=None)

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
        TestRunReasonerCycleAwaitingLLM coverage below -- proving the two
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

        with patch.object(reasoner_signals_module, "resolve_llm_vote", return_value=InvestigationState.DEEPENING):
            outcome = run_reasoner_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=None)

        assert outcome.decision == "repeat_deepen"
        assert state.active_investigation_anchor["state"] == InvestigationState.DEEPENING.value


class TestRunReasonerCycleAwaitingLLM:
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

        with patch.object(reasoner_signals_module, "resolve_llm_vote", return_value=InvestigationState.SATISFIED) as mock_vote:
            outcome = run_reasoner_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=None)

        mock_vote.assert_called_once()
        assert outcome.decision == "advance"

    def test_resolve_llm_vote_no_port_returns_exploring_sentinel(self):
        # A202 shipped resolve_llm_vote as a loud NotImplementedError
        # placeholder specifically for A205 to fill in (see this module's
        # own docstring above). A205 replaced it with the real bounded LLM
        # call; a missing llm_port is now one of its handled failure paths,
        # resolving to InvestigationState.EXPLORING (guaranteed outside
        # permissible_llm_transitions -- see tests/test_a205_reasoner_error_
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
