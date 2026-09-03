"""Tests for A249: `action_space_exhausted` previously shared workflow.py's
`termination_from_evaluation`/`PhaseStatus.TERMINATE` short-circuit with
`terminal_reason` (the ARC API's own authoritative win/loss signal),
ending the whole episode before Annatar -- the codebase's single
designated decision-owner -- ever got a say. See backlog/A249.md.

Test groups:
  - Evaluator: action_space_exhausted no longer produces decision=TERMINATE
    (visibility -- metadata/exhaustion_source -- unchanged); terminal_reason
    is untouched (regression guard, written first, confirmed to pass against
    the current code before this card's fix too -- see the class docstring).
  - Orchestrator (Annatar configured): action_space_exhausted folds into the
    same stall_reason channel check_stall already uses; the episode does NOT
    terminate independently -- Annatar's own decision (e.g. advance to a
    fresh anchor) drives what happens next.
  - Orchestrator (Annatar NOT configured, legacy fallback): action_space_
    exhausted still terminates the episode immediately, byte-for-byte with
    pre-A249 behavior -- mirrors A221's explicit "genuinely separate
    consumer" precedent.
  - Probe-path call site: the same fold applies to the readiness-gate probe
    path's own self._dependencies.annatar(...) call.
  - Whole-episode futility (annatar_unproductive_anchor_streak, A230) is
    unaffected -- this card relocates *where* action_space_exhausted's
    termination decision is made, it does not remove the ability to end a
    genuinely-exhausted episode.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.annatar_signals import classify_all_entity_domains
from agents.arc4.annatar_state_machine import CynefinDomain, ReadinessStatus
from agents.arc4.evaluator import EvaluationLimits, Evaluator
from agents.arc4.plan_generator import PlanGenerator
from agents.arc4.types import (
    AnnatarOutcome,
    EvaluationResult,
    ExecutionResult,
    GoalHypothesis,
    PerceptionSnapshot,
    PhaseResult,
    PhaseStatus,
    PlanCandidate,
    ResolvedGoal,
    VetDecision,
    WorkflowDecision,
    WorkflowPhase,
    WorkflowState,
    WorkflowStatus,
)
from agents.arc4.ports import WorkflowDependencies
from agents.arc4.workflow import WorkflowLimits, WorkflowOrchestrator

from test_arc4_workflow import (
    _dependencies as _shared_dependencies,
    _evaluation,
    _execute,
    _goal,
    _perception,
    _plan,
    _vet,
)


# --- Evaluator-level unit tests ---------------------------------------------


def _goal_payload(goal_id: str = "goal-1") -> ResolvedGoal:
    return ResolvedGoal(selected=GoalHypothesis(goal_id=goal_id, description=goal_id, confidence=0.8))


def _perception_snapshot() -> PerceptionSnapshot:
    return PerceptionSnapshot(observation={"grid": [[1]]}, grid_hash="grid-1")


def _execution(
    action_id: str = "move-right",
    *,
    metadata: dict | None = None,
) -> ExecutionResult:
    candidate = PlanCandidate(action_id=action_id, expected_effect="shift")
    return ExecutionResult(
        action_id=action_id,
        candidate=candidate,
        observation={"grid": [[1]]},
        did_progress=False,
        predicted_effect="shift",
        actual_effect="shift",
        metadata=metadata or {},
    )


class TestEvaluatorNoLongerTerminatesOnActionSpaceExhausted:
    def test_threshold_only_exhaustion_stays_continue(self):
        """No graph port -> threshold_only source -- must no longer set
        decision=TERMINATE."""
        state = WorkflowState()
        state.action_attempt_counts["move-right"] = 5
        evaluator = Evaluator(graph_query_port=None, limits=EvaluationLimits(exhausted_action_attempt_threshold=4))

        result = evaluator.evaluate(state, _perception_snapshot(), _goal_payload(), _execution())

        assert result.payload.decision == WorkflowDecision.CONTINUE
        assert result.status == PhaseStatus.OK
        assert result.payload.reason == "action_space_exhausted"
        assert result.payload.metadata["action_space_exhausted"] is True
        assert result.payload.metadata["exhaustion_source"] == "threshold_only"

    def test_graph_confirmed_exhaustion_stays_continue(self):
        state = WorkflowState()
        state.action_attempt_counts["move-right"] = 5
        graph = MagicMock()
        graph.fetch_untested_actions.return_value = []
        evaluator = Evaluator(graph_query_port=graph, limits=EvaluationLimits(exhausted_action_attempt_threshold=4))

        result = evaluator.evaluate(state, _perception_snapshot(), _goal_payload(), _execution())

        assert result.payload.decision == WorkflowDecision.CONTINUE
        assert result.status == PhaseStatus.OK
        assert result.payload.metadata["action_space_exhausted"] is True
        assert result.payload.metadata["exhaustion_source"] == "graph_confirmed_no_untested"

    def test_env_reported_exhaustion_stays_continue(self):
        """Dead code in production today (see backlog/A249.md's grep), but
        evaluator.py's own detection branch still exists -- confirm this
        card's fix covers it too, not just the two reachable sources."""
        state = WorkflowState()
        evaluator = Evaluator(graph_query_port=None)
        execution = _execution(metadata={"action_space_exhausted": True})

        result = evaluator.evaluate(state, _perception_snapshot(), _goal_payload(), execution)

        assert result.payload.decision == WorkflowDecision.CONTINUE
        assert result.status == PhaseStatus.OK
        assert result.payload.metadata["exhaustion_source"] == "env_reported"

    def test_not_exhausted_unaffected(self):
        """Regression: an evaluation that is not exhausted at all must be
        completely unaffected by this card."""
        evaluator = Evaluator(graph_query_port=None, limits=EvaluationLimits(exhausted_action_attempt_threshold=4))

        result = evaluator.evaluate(WorkflowState(), _perception_snapshot(), _goal_payload(), _execution())

        assert result.payload.metadata["action_space_exhausted"] is False
        assert "exhaustion_source" not in result.payload.metadata


class TestTerminalReasonUnaffected:
    """The single most important regression guard in this card: a genuine
    ARC-API WIN/GAME_OVER must still produce decision=TERMINATE,
    unconditionally, completely unaffected by this change. Written first and
    confirmed to pass against the pre-A249 evaluator.py too (terminal_reason's
    own branch is untouched by this card's edit -- it is the `if` branch, not
    the `elif action_space_exhausted` branch that was changed)."""

    def test_genuine_win_still_terminates(self):
        state = WorkflowState()
        evaluator = Evaluator(graph_query_port=None)
        execution = _execution(metadata={"solved": True})

        result = evaluator.evaluate(state, _perception_snapshot(), _goal_payload(), execution)

        assert result.payload.decision == WorkflowDecision.TERMINATE
        assert result.status == PhaseStatus.TERMINATE
        assert result.payload.reason == "solved"

    def test_terminal_reason_wins_even_when_action_space_also_exhausted(self):
        """Both conditions true in the same cycle -- terminal_reason (the
        environment's own authoritative signal) must still take priority,
        exactly as the pre-existing if/elif ordering already guaranteed."""
        state = WorkflowState()
        state.action_attempt_counts["move-right"] = 5
        evaluator = Evaluator(graph_query_port=None, limits=EvaluationLimits(exhausted_action_attempt_threshold=4))
        execution = _execution(metadata={"game_over": True})

        result = evaluator.evaluate(state, _perception_snapshot(), _goal_payload(), execution)

        assert result.payload.decision == WorkflowDecision.TERMINATE
        assert result.payload.reason == "game_over"


# --- Orchestrator-level integration tests -----------------------------------


def _evaluation_with_action_space_exhausted(*, meaningful_progress: bool = False) -> PhaseResult[EvaluationResult]:
    """Simulates evaluator.py's POST-A249 output shape for an exhausted
    cycle: decision=CONTINUE (not TERMINATE), reason/metadata carrying the
    exhaustion flag -- exactly what the real Evaluator now produces."""
    return PhaseResult(
        phase=WorkflowPhase.EVALUATE,
        status=PhaseStatus.OK,
        payload=EvaluationResult(
            decision=WorkflowDecision.CONTINUE,
            meaningful_progress=meaningful_progress,
            reason="action_space_exhausted",
            metadata={"action_space_exhausted": True, "exhaustion_source": "threshold_only", "grid_changed": False},
        ),
        reason="action_space_exhausted",
    )


class TestAnnatarConfiguredRoutesExhaustionInsteadOfTerminating:
    def test_action_space_exhausted_does_not_terminate_when_annatar_configured(self):
        """The core acceptance criterion: with Annatar configured, an
        action_space_exhausted=True cycle must NOT end the episode via the
        old independent-termination path. Annatar's own (mocked) decision
        drives what happens instead."""
        calls: list[str] = []
        mock_annatar = MagicMock(return_value=AnnatarOutcome(decision="advance"))
        deps = _shared_dependencies(
            calls,
            overrides={
                "perceive": [_perception("grid-1"), _perception("grid-2")],
                "resolve": [_goal(), _goal()],
                "plan": [_plan(), _plan()],
                "vet": [_vet(True), _vet(True)],
                "execute": [_execute(grid_hash="grid-2"), _execute(grid_hash="grid-3")],
                "evaluate": [
                    _evaluation_with_action_space_exhausted(),
                    _evaluation(WorkflowDecision.TERMINATE, meaningful_progress=True, reason="done"),
                ],
            },
        )
        deps.annatar = mock_annatar

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=5)).run(WorkflowState(), {"grid": [[1]]})

        # Must NOT have ended the episode on cycle 1's exhaustion signal --
        # the run continued into cycle 2 (perceive/resolve/plan/vet/execute/
        # evaluate all ran a second time) and ended via the SECOND cycle's
        # genuine terminal reason instead.
        assert result.status == WorkflowStatus.TERMINATED
        assert result.reason == "done"
        assert result.completed_cycles == 2
        # Annatar is called once for cycle 1 (the exhausted, non-terminal
        # cycle) -- cycle 2's genuine terminal_reason short-circuits BEFORE
        # Annatar is invoked at all, exactly as it must (the critical
        # regression guard this card protects).
        assert mock_annatar.call_count == 1

    def test_action_space_exhausted_folds_into_stall_reason_channel(self):
        """Confirms the exact mechanism: action_space_exhausted reaches
        Annatar via the same stall_reason kwarg check_stall's own signal
        already uses (annatar_signals.py:251's `is not None` override)."""
        calls: list[str] = []
        mock_annatar = MagicMock(return_value=AnnatarOutcome(decision="terminate"))
        deps = _shared_dependencies(
            calls,
            overrides={"evaluate": [_evaluation_with_action_space_exhausted()]},
        )
        deps.annatar = mock_annatar

        WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(WorkflowState(), {"grid": [[1]]})

        assert mock_annatar.call_count == 1
        assert mock_annatar.call_args_list[0].kwargs["stall_reason"] == "action_space_exhausted"

    def test_check_stalls_own_stall_reason_still_wins_when_both_present(self):
        """When check_stall ALSO fires this cycle (a real, independent
        signal), the OR-fold must prefer it over the synthetic
        "action_space_exhausted" string -- stall_reason's actual string
        value is never read by any consumer (only `is not None`), but this
        pins the fold's exact precedence so a future consumer that does
        start reading the value sees the more specific check_stall reason.

        check_stall needs 2 consecutive no-progress cycles to fire here:
        num_available_actions is 0 (no "available_actions" key in the
        scripted observations), so stall_threshold falls back to
        max_consecutive_no_progress=2 (the default) even though this test
        also sets it explicitly for clarity."""
        calls: list[str] = []
        mock_annatar = MagicMock(
            side_effect=[AnnatarOutcome(decision="advance"), AnnatarOutcome(decision="terminate")]
        )
        deps = _shared_dependencies(
            calls,
            overrides={
                "perceive": [_perception("grid-1"), _perception("grid-2")],
                "resolve": [_goal(), _goal()],
                "plan": [_plan(), _plan()],
                "vet": [_vet(True), _vet(True)],
                "execute": [_execute(grid_hash="grid-2"), _execute(grid_hash="grid-3")],
                "evaluate": [
                    _evaluation_with_action_space_exhausted(),
                    _evaluation_with_action_space_exhausted(),
                ],
            },
        )
        deps.annatar = mock_annatar

        WorkflowOrchestrator(
            deps, limits=WorkflowLimits(max_cycles=3, max_consecutive_no_progress=2)
        ).run(WorkflowState(), {"grid": [[1]]})

        assert mock_annatar.call_count == 2
        first_call, second_call = mock_annatar.call_args_list
        # Cycle 1: consecutive_no_progress_count is 1 after this cycle's own
        # bookkeeping runs -- below the threshold of 2 -- so check_stall
        # itself hasn't fired yet; only action_space_exhausted has.
        assert first_call.kwargs["stall_reason"] == "action_space_exhausted"
        # Cycle 2: consecutive_no_progress_count reaches 2 -- check_stall's
        # own "stall_detected" now fires too, and must win the OR.
        assert second_call.kwargs["stall_reason"] == "stall_detected"

    def test_annatar_decision_end_to_end_produces_exhausted_advance_not_termination(self):
        """End-to-end proof (not just a mocked Annatar) that an
        action_space_exhausted=True cycle drives annatar_state_machine's own
        real transition() to anchor-scoped EXHAUSTED->ADVANCE rather than an
        episode-ending TERMINATE -- using the real annatar_signals.
        run_annatar_cycle wired in as the `annatar` dependency, exactly as
        arc_runtime/bundle.py wires it in production."""
        from agents.arc4.annatar_signals import run_annatar_cycle

        calls: list[str] = []

        def real_annatar(state, perception, execution, evaluation, **kwargs):
            return run_annatar_cycle(state, perception, execution, evaluation, graph_port=None, **kwargs)

        deps = _shared_dependencies(
            calls,
            overrides={
                "perceive": [_perception("grid-1"), _perception("grid-2")],
                "resolve": [_goal(), _goal()],
                "plan": [_plan(), _plan()],
                "vet": [_vet(True), _vet(True)],
                "execute": [_execute(grid_hash="grid-2"), _execute(grid_hash="grid-3")],
                "evaluate": [
                    _evaluation_with_action_space_exhausted(),
                    _evaluation(WorkflowDecision.TERMINATE, meaningful_progress=True, reason="done"),
                ],
            },
        )
        deps.annatar = real_annatar

        state = WorkflowState(active_goal=_goal_payload())
        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=5)).run(state, {"grid": [[1]]})

        # The episode did NOT end on cycle 1's exhaustion -- it advanced to
        # a fresh anchor (active_investigation_anchor cleared by the real
        # ADVANCE path) and continued into cycle 2, ending there instead via
        # the genuine "done" terminal reason.
        assert result.status == WorkflowStatus.TERMINATED
        assert result.reason == "done"
        assert result.completed_cycles == 2


class TestNoAnnatarConfiguredPreservesExactPriorBehavior:
    """Legacy fallback: with no Annatar wired in, this card explicitly does
    NOT change behavior (A249.md's "Explicitly NOT this card's job" +
    A221's precedent). action_space_exhausted must still terminate the
    episode immediately, byte-for-byte with the pre-A249 outcome."""

    def test_action_space_exhausted_still_terminates_with_no_annatar(self):
        calls: list[str] = []
        deps = _shared_dependencies(
            calls,
            overrides={"evaluate": [_evaluation_with_action_space_exhausted()]},
        )
        # deps.annatar stays None (the default from _shared_dependencies).

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(WorkflowState(), {"grid": [[1]]})

        assert result.status == WorkflowStatus.TERMINATED
        assert result.reason == "action_space_exhausted"
        assert result.completed_cycles == 1
        assert calls == ["perceive", "resolve", "plan", "vet", "execute", "evaluate"]


class TestGenuineTerminalReasonStillShortCircuitsUnconditionally:
    """The critical regression guard at the orchestrator level: a real
    terminal decision (WIN/GAME_OVER, simulated here the same way every
    other workflow.py test does -- a scripted evaluate() PhaseResult with
    decision=TERMINATE) must still short-circuit immediately, with Annatar
    never invoked, completely unaffected by this card."""

    def test_terminal_decision_short_circuits_before_annatar_with_annatar_configured(self):
        calls: list[str] = []
        mock_annatar = MagicMock(return_value=AnnatarOutcome(decision="advance"))
        deps = _shared_dependencies(
            calls,
            overrides={"evaluate": [_evaluation(WorkflowDecision.TERMINATE, meaningful_progress=True, reason="done")]},
        )
        deps.annatar = mock_annatar

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(WorkflowState(), {"grid": [[1]]})

        assert result.status == WorkflowStatus.TERMINATED
        assert result.reason == "done"
        assert mock_annatar.call_count == 0

    def test_terminal_decision_short_circuits_with_no_annatar_configured(self):
        calls: list[str] = []
        deps = _shared_dependencies(
            calls,
            overrides={"evaluate": [_evaluation(WorkflowDecision.TERMINATE, meaningful_progress=True, reason="solved")]},
        )

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(WorkflowState(), {"grid": [[1]]})

        assert result.status == WorkflowStatus.TERMINATED
        assert result.reason == "solved"


# --- Probe-path call site ----------------------------------------------------


def _entity(entity_ref: int, **overrides) -> "PerceivedEntity":
    from agents.arc4.types import PerceivedEntity

    attrs = {"entity_ref": entity_ref, "coverage": 0.05, "cell_count": 3, "centroid": (10.0, 10.0 + entity_ref)}
    attrs.update(overrides)
    return PerceivedEntity(kind="point", value="5", attributes=attrs)


def _probe_dependencies(*, graph_port, annatar, evaluate_fn):
    """Mirrors test_a224_workflow_readiness_integration.py's
    _make_dependencies pattern (the established fixture for routing through
    the probe path), trimmed to only what this card's tests need: a
    NOT_READY gate (one DISORDER entity) so every cycle takes the probe
    path, plus a caller-supplied evaluate() so exhaustion can be injected."""

    def perceive(state, observation):
        return PhaseResult(
            phase=WorkflowPhase.PERCEIVE,
            status=PhaseStatus.OK,
            payload=PerceptionSnapshot(observation={}, grid_hash="h1", entities=observation.get("entities", ())),
        )

    def readiness_gate(state, perception):
        domains = classify_all_entity_domains(perception, graph_port)
        entities_total = len(domains)
        entities_mapped = sum(1 for d in domains.values() if d != CynefinDomain.DISORDER)
        status = ReadinessStatus.READY if entities_mapped == entities_total else ReadinessStatus.NOT_READY
        probe_candidate = None
        if status == ReadinessStatus.NOT_READY:
            probe_candidate = PlanGenerator()._select_readiness_probe(perception, domains, untested_non_click_actions=())
        return PhaseResult(
            phase=WorkflowPhase.READINESS_GATE,
            status=PhaseStatus.OK,
            payload={
                "status": status,
                "entity_domains": domains,
                "entities_mapped": entities_mapped,
                "entities_total": entities_total,
                "probe_candidate": probe_candidate,
                "untested_non_click_actions": [],
            },
        )

    def resolve(state, perception):
        raise AssertionError("resolve must not be called during the probe path")

    def plan(state, perception, resolved_goal):
        raise AssertionError("plan must not be called during the probe path")

    def vet(state, perception, resolved_goal, planning):
        raise AssertionError("vet must not be called during the probe path")

    def execute(state, perception, resolved_goal, vet_decision):
        candidate = PlanCandidate(action_id="ACTION6", goal_id="readiness_probe", payload={"x": 1, "y": 1})
        return PhaseResult(
            phase=WorkflowPhase.EXECUTE,
            status=PhaseStatus.OK,
            payload=ExecutionResult(action_id="ACTION6", candidate=candidate, observation={}, did_progress=False),
        )

    return WorkflowDependencies(
        perceive=perceive,
        resolve=resolve,
        plan=plan,
        vet=vet,
        execute=execute,
        evaluate=evaluate_fn,
        annatar=annatar,
        readiness_gate=readiness_gate,
    )


class TestProbePathCallSiteAlsoRoutesExhaustionThroughAnnatar:
    def test_probe_path_action_space_exhausted_reaches_annatar_stall_reason(self):
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {"hypotheses": [], "rules": []}
        graph_port.fetch_entity_history.return_value = {"transitions": [], "changed_count_total": 0}
        # decision="terminate" so the probe-path block returns immediately
        # (see workflow.py's `if outcome.decision == "terminate":` branch)
        # instead of `continue`-looping the probe forever with
        # exploration_complete=False -- this test only needs to observe the
        # one call's kwargs, not drive a multi-cycle probe scenario.
        mock_annatar = MagicMock(return_value=AnnatarOutcome(decision="terminate"))

        def evaluate(state, perception, resolved_goal, execution):
            return PhaseResult(
                phase=WorkflowPhase.EVALUATE,
                status=PhaseStatus.OK,
                payload=EvaluationResult(
                    decision=WorkflowDecision.CONTINUE,
                    meaningful_progress=False,
                    reason="action_space_exhausted",
                    metadata={"action_space_exhausted": True, "exhaustion_source": "threshold_only"},
                ),
            )

        deps = _probe_dependencies(graph_port=graph_port, annatar=mock_annatar, evaluate_fn=evaluate)
        orchestrator = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=1))

        orchestrator.run(WorkflowState(), {"entities": (_entity(1),)})

        assert mock_annatar.call_count == 1
        assert mock_annatar.call_args_list[0].kwargs["stall_reason"] == "action_space_exhausted"

    def test_probe_path_action_space_exhausted_terminates_with_no_annatar(self):
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {"hypotheses": [], "rules": []}
        graph_port.fetch_entity_history.return_value = {"transitions": [], "changed_count_total": 0}

        def evaluate(state, perception, resolved_goal, execution):
            return PhaseResult(
                phase=WorkflowPhase.EVALUATE,
                status=PhaseStatus.OK,
                payload=EvaluationResult(
                    decision=WorkflowDecision.CONTINUE,
                    meaningful_progress=False,
                    reason="action_space_exhausted",
                    metadata={"action_space_exhausted": True, "exhaustion_source": "threshold_only"},
                ),
            )

        deps = _probe_dependencies(graph_port=graph_port, annatar=None, evaluate_fn=evaluate)
        orchestrator = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3))

        result = orchestrator.run(WorkflowState(), {"entities": (_entity(1),)})

        assert result.status == WorkflowStatus.TERMINATED
        assert result.reason == "action_space_exhausted"


# --- Whole-episode futility backstop is unaffected --------------------------


class TestWholeEpisodeFutilityBackstopStillWorks:
    """This card relocates *where* action_space_exhausted's termination
    decision is made -- it must not remove the episode's ability to end when
    genuinely exhausted across every anchor. annatar_unproductive_anchor_
    streak (A230) is the existing, unmodified mechanism for that; this test
    confirms an action_space_exhausted-driven ADVANCE still counts toward
    it and can still terminate the episode once the streak threshold is
    crossed."""

    def test_repeated_action_space_exhausted_advances_eventually_terminate_via_streak(self):
        """Mirrors test_a202_annatar_orchestrator_integration.py's own
        TestRunAnnatarCycleWholeEpisodeFutility::_unproductive_advance
        pattern exactly (same CHAOTIC-domain-via-entity-anchor mechanism,
        unmodified by this card -- A221 already established that
        all_falsified/stall_reason folding carries no EXHAUSTED-triggering
        weight on its own; CynefinDomain.CHAOTIC does that work). The only
        thing this test changes from that existing pattern is the
        stall_reason string passed in: "action_space_exhausted" instead of
        "stalled" -- confirming this card's new signal drives the exact
        same, unmodified whole-episode-futility backstop."""
        from agents.arc4.annatar_signals import DEFAULT_MAX_UNPRODUCTIVE_ANCHORS, run_annatar_cycle
        from agents.arc4.annatar_state_machine import InvestigationState
        from agents.arc4.types import PlanCandidate

        def _unproductive_advance(state):
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
            execution = ExecutionResult(action_id="a1", candidate=candidate, observation={"grid": "h2"})
            evaluation = EvaluationResult(
                decision=WorkflowDecision.CONTINUE,
                meaningful_progress=False,
                reason="action_space_exhausted",
                metadata={"grid_changed": True, "action_space_exhausted": True},
            )
            graph_port = MagicMock()
            graph_port.fetch_entity_neighborhood.return_value = {
                "hypotheses": [],
                "rules": [{"confidence": 0.0, "falsified": True, "to_color": 5}],
            }
            graph_port.fetch_untested_actions.return_value = []
            return run_annatar_cycle(
                state, _perception_snapshot(), execution, evaluation,
                graph_port=graph_port, stall_reason="action_space_exhausted",
            )

        state = WorkflowState(active_goal=_goal_payload("g7"))
        n = DEFAULT_MAX_UNPRODUCTIVE_ANCHORS

        outcomes = [_unproductive_advance(state) for _ in range(n)]

        assert [o.decision for o in outcomes] == ["advance"] * (n - 1) + ["terminate"]
        assert state.annatar_unproductive_anchor_streak == n
        assert state.active_investigation_anchor is None

