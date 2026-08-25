"""A205: explicit, tested error handling for the trajectory Annatar
(docs/superpowers/specs/2026-08-23-trajectory-reasoner-design.md, section 8).

Two failure modes:
  1. Graph unreachable mid-cycle -- must degrade to a safe, non-crashing
     decision, and the degradation must be visible in telemetry rather than
     silently swallowed (AnnatarOutcome.degraded -> WorkflowState
     .annatar_degraded -> telemetry.py::_step_snapshot's "annatar_degraded"
     field, mirroring the exact plumbing pattern A196/A197 already
     established for llm_escalated_plan/graph_grounded/exhaustion_source/
     capability_missing_count).
  2. AWAITING_LLM escalation failure (no llm_port, an exception from the LLM
     call, or an unparseable response) -- A202 shipped resolve_llm_vote as a
     loud NotImplementedError placeholder specifically for this card. The
     real implementation must resolve every failure path through
     InvestigationState.EXPLORING, the sentinel A200's own
     permissible_llm_transitions() guarantees is never a legal AWAITING_LLM
     vote -- so apply_llm_vote's existing out-of-set-vote fallback (prefer
     EXHAUSTED when the graph permits it, else DEEPENING) does the actual
     fallback work, with no second, bespoke fallback rule introduced here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.annatar_state_machine import CycleSignals, InvestigationState, permissible_llm_transitions
from agents.arc4.annatar_signals import compute_cycle_signals, resolve_llm_vote, run_annatar_cycle
from agents.arc4.telemetry import ArcV2Telemetry
from agents.arc4.types import (
    EvaluationResult,
    ExecutionResult,
    GoalHypothesis,
    PerceptionSnapshot,
    PlanCandidate,
    ResolvedGoal,
    WorkflowDecision,
    WorkflowState,
)


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


def _fresh_state(goal_id: str = "g1") -> WorkflowState:
    return WorkflowState(active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id=goal_id, description="d")))


def _entity_anchored_state() -> WorkflowState:
    return WorkflowState(
        active_investigation_anchor={
            "anchor_ref": "e1",
            "anchor_type": "entity",
            "thread_id": "t1",
            "state": InvestigationState.DEEPENING.value,
            "deepening_cycle_count": 1,
            "already_retried": False,
        }
    )


def _signals(**overrides) -> CycleSignals:
    base = dict(
        meaningful_progress=False,
        confidence=0.0,
        untested_remaining=True,
        all_falsified=False,
        execution_inconclusive=False,
        deepening_cycle_count=0,
        already_retried=False,
    )
    base.update(overrides)
    return CycleSignals(**base)


# ── Failure mode 1: graph unreachable mid-cycle ──────────────────────────


class TestGraphUnreachableDegradesSafely:
    def test_graph_exception_sets_outcome_degraded_true_with_valid_decision(self):
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.side_effect = RuntimeError("graph down")
        graph_port.fetch_untested_actions.side_effect = RuntimeError("graph down")
        graph_port.start_or_resume_thread.side_effect = RuntimeError("graph down")

        state = _fresh_state()
        candidate = PlanCandidate(action_id="a1", goal_id="g1", metadata={"entity_ref": "e1"})
        execution = _execution_result(action_id="a1", candidate=candidate)
        evaluation = _evaluation_result(meaningful_progress=False, grid_changed=True)

        outcome = run_annatar_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=graph_port)

        assert outcome.degraded is True
        assert outcome.decision in ("advance", "repeat_deepen", "repeat_retry", "terminate")

    def test_write_thread_state_failure_also_sets_degraded_true(self):
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {"hypotheses": [], "rules": []}
        graph_port.fetch_untested_actions.return_value = ["ACTION1"]
        graph_port.write_thread_state.side_effect = RuntimeError("graph down")

        state = _entity_anchored_state()
        execution = _execution_result()
        evaluation = _evaluation_result(meaningful_progress=False, grid_changed=True)

        outcome = run_annatar_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=graph_port)

        assert outcome.degraded is True

    def test_normal_successful_cycle_produces_degraded_false(self):
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {"hypotheses": [], "rules": []}
        graph_port.fetch_untested_actions.return_value = ["ACTION1"]
        graph_port.start_or_resume_thread.return_value = {"thread_id": "t1"}
        graph_port.write_thread_state.return_value = {"ok": True}

        state = _fresh_state()
        candidate = PlanCandidate(action_id="a1", goal_id="g1", metadata={"entity_ref": "e1"})
        execution = _execution_result(action_id="a1", candidate=candidate)
        evaluation = _evaluation_result(meaningful_progress=False, grid_changed=True)

        outcome = run_annatar_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=graph_port)

        assert outcome.degraded is False

    def test_no_graph_port_configured_is_not_treated_as_degraded(self):
        state = _fresh_state()
        execution = _execution_result()
        evaluation = _evaluation_result(meaningful_progress=False, grid_changed=True)

        outcome = run_annatar_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=None)

        assert outcome.degraded is False

    def test_compute_cycle_signals_marks_degraded_on_graph_exception(self):
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

        assert signals.degraded is True
        # A202's existing safe-default behavior must be unchanged.
        assert signals.confidence == 0.0
        assert signals.untested_remaining is True

    def test_compute_cycle_signals_not_degraded_when_graph_calls_succeed(self):
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {"hypotheses": [], "rules": []}
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

        assert signals.degraded is False


class TestTelemetryReflectsAnnatarDegraded:
    def test_annatar_degraded_true_in_step_snapshot(self):
        telemetry = ArcV2Telemetry(task_id="test_task", game_id="test_game", append_snapshot=None)
        state = WorkflowState()
        state.annatar_degraded = True

        snapshot = telemetry._step_snapshot((state,))

        assert "annatar_degraded" in snapshot
        assert snapshot["annatar_degraded"] is True

    def test_annatar_degraded_false_in_step_snapshot(self):
        telemetry = ArcV2Telemetry(task_id="test_task", game_id="test_game", append_snapshot=None)
        state = WorkflowState()
        state.annatar_degraded = False

        snapshot = telemetry._step_snapshot((state,))

        assert "annatar_degraded" in snapshot
        assert snapshot["annatar_degraded"] is False

    def test_annatar_degraded_defaults_false_when_no_annatar_configured(self):
        """No Annatar means WorkflowState.annatar_degraded is never touched
        from its dataclass default -- the same "no Annatar -> safe value"
        degrade pattern A196/A197 already use for llm_escalated_plan/
        graph_grounded/exhaustion_source."""
        telemetry = ArcV2Telemetry(task_id="test_task", game_id="test_game", append_snapshot=None)
        state = WorkflowState()  # untouched, default

        snapshot = telemetry._step_snapshot((state,))

        assert snapshot["annatar_degraded"] is False

    def test_annatar_degraded_defaults_false_when_state_arg_missing(self):
        telemetry = ArcV2Telemetry(task_id="test_task", game_id="test_game", append_snapshot=None)

        snapshot = telemetry._step_snapshot(())

        assert snapshot["annatar_degraded"] is False


# ── Failure mode 2: AWAITING_LLM escalation failure ──────────────────────


class TestResolveLlmVoteNoPort:
    def test_no_llm_port_returns_exploring_directly_no_call_attempted(self):
        signals = _signals()
        vote = resolve_llm_vote(None, WorkflowState(), signals)
        assert vote == InvestigationState.EXPLORING


class TestResolveLlmVoteFailureModes:
    def test_llm_call_raising_resolves_to_exploring(self):
        llm_port = MagicMock()
        llm_port.chat.side_effect = RuntimeError("timeout")
        signals = _signals()

        vote = resolve_llm_vote(llm_port, WorkflowState(), signals)

        assert vote == InvestigationState.EXPLORING

    def test_unparseable_response_resolves_to_exploring(self):
        llm_port = MagicMock()
        llm_port.chat.return_value = "not json and no recognizable state field"
        signals = _signals()

        vote = resolve_llm_vote(llm_port, WorkflowState(), signals)

        assert vote == InvestigationState.EXPLORING

    def test_exploring_is_never_in_permissible_llm_transitions(self):
        """The load-bearing fact that makes the failure-sentinel approach
        correct: EXPLORING must never be a member of permissible_llm_
        transitions()'s result, for any signal combination, so it always
        triggers apply_llm_vote's out-of-set fallback rather than being
        silently accepted as a real vote."""
        for all_falsified in (True, False):
            for untested_remaining in (True, False):
                signals = _signals(all_falsified=all_falsified, untested_remaining=untested_remaining)
                assert InvestigationState.EXPLORING not in permissible_llm_transitions(signals)

    def test_failure_routes_through_apply_llm_vote_fallback_to_exhausted_when_permitted(self):
        # untested_remaining=False -> EXHAUSTED is graph-permitted.
        llm_port = MagicMock()
        llm_port.chat.side_effect = RuntimeError("timeout")
        state = WorkflowState(
            active_investigation_anchor={
                "anchor_ref": "g1",
                "anchor_type": "goal",
                "thread_id": None,
                "state": InvestigationState.AWAITING_LLM.value,
                "deepening_cycle_count": 5,
                "already_retried": False,
            }
        )
        candidate = PlanCandidate(action_id="a1", goal_id="g1")
        execution = _execution_result(action_id="a1", candidate=candidate)
        # grid_changed=False, meaningful_progress=False -> execution_inconclusive
        # would normally trigger RETRY from EXPLORING/DEEPENING, but current_state
        # is AWAITING_LLM here, so transition() is bypassed entirely.
        evaluation = _evaluation_result(meaningful_progress=False, grid_changed=True)

        graph_port = MagicMock()
        graph_port.fetch_untested_actions.return_value = []  # nothing untested -> EXHAUSTED permitted

        outcome = run_annatar_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=graph_port)

        # EXHAUSTED -> decision_for_state -> "advance"
        assert outcome.decision == "advance"

    def test_failure_routes_through_apply_llm_vote_fallback_to_deepening_when_exhausted_not_permitted(self):
        llm_port = MagicMock()
        llm_port.chat.side_effect = RuntimeError("timeout")
        state = WorkflowState(
            active_investigation_anchor={
                "anchor_ref": "g1",
                "anchor_type": "goal",
                "thread_id": None,
                "state": InvestigationState.AWAITING_LLM.value,
                "deepening_cycle_count": 5,
                "already_retried": False,
            }
        )
        candidate = PlanCandidate(action_id="a1", goal_id="g1")
        execution = _execution_result(action_id="a1", candidate=candidate)
        evaluation = _evaluation_result(meaningful_progress=False, grid_changed=True)

        graph_port = MagicMock()
        graph_port.fetch_untested_actions.return_value = ["ACTION1"]  # untested remains -> EXHAUSTED not permitted

        outcome = run_annatar_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=graph_port)

        # DEEPENING -> decision_for_state -> "repeat_deepen"
        assert outcome.decision == "repeat_deepen"
        assert state.active_investigation_anchor["state"] == InvestigationState.DEEPENING.value


class TestResolveLlmVoteSuccess:
    def test_successful_call_with_permitted_state_is_used_directly(self):
        llm_port = MagicMock()
        llm_port.chat.return_value = '{"state": "deepening", "reason": "still ambiguous"}'
        signals = _signals(untested_remaining=True, all_falsified=False)

        vote = resolve_llm_vote(llm_port, WorkflowState(), signals)

        assert vote == InvestigationState.DEEPENING
        llm_port.chat.assert_called_once()

    def test_successful_vote_flows_through_run_annatar_cycle_without_degrading(self):
        llm_port = MagicMock()
        llm_port.chat.return_value = '{"state": "satisfied", "reason": "confidence high"}'
        state = WorkflowState(
            active_investigation_anchor={
                "anchor_ref": "g1",
                "anchor_type": "goal",
                "thread_id": None,
                "state": InvestigationState.AWAITING_LLM.value,
                "deepening_cycle_count": 5,
                "already_retried": False,
            }
        )
        candidate = PlanCandidate(action_id="a1", goal_id="g1")
        execution = _execution_result(action_id="a1", candidate=candidate)
        evaluation = _evaluation_result(meaningful_progress=False, grid_changed=True)

        outcome = run_annatar_cycle(
            state, _perception_snapshot(), execution, evaluation, graph_port=None, llm_port=llm_port
        )

        assert outcome.decision == "advance"  # SATISFIED -> advance
        assert outcome.degraded is False
        llm_port.chat.assert_called_once()
