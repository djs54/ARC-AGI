"""Thin ARC v2 workflow orchestrator."""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import Any, Mapping

from .annatar_state_machine import ReadinessStatus
from .cycle_policy import check_budget, check_stall, count_base_actions, record_evaluation_outcome, stall_threshold, termination_from_evaluation
from .ports import WorkflowDependencies
from .types import (
    EvaluationResult,
    ExecutionResult,
    GoalHypothesis,
    PhaseResult,
    PhaseStatus,
    ResolvedGoal,
    VetDecision,
    WorkflowDecision,
    WorkflowPhase,
    WorkflowRunResult,
    WorkflowState,
    WorkflowStatus,
)


def wrap_execute_with_write_ahead(execute: Any, graph_port: Any) -> Any:
    """A204 / spec section 7: bracket an ExecutePhase callable with
    write-ahead cycle recording. `execute` is called synchronously and
    returns only after the real, live ARC API call has completed -- this is
    the one place in the whole trajectory-Annatar family (A200-A206) where
    a bug can mean double-acting on non-idempotent external game state, so
    the invariant here is non-negotiable: `write_cycle` failing (any
    exception, or a missing `cycle_id`) must NEVER prevent `execute` from
    running. The durability write is a safety net around the real action,
    not a gate in front of it.

    This wrapping is applied to the `execute` *dependency* itself, at
    bundle-build time in arc_runtime/bundle.py -- the same closure-over-
    graph_port pattern every other phase (resolve/plan/reason) already
    uses, per ports.py's AnnatarPhase docstring: "WorkflowOrchestrator
    itself does not need to hold a graph_port reference." WorkflowOrchestrator
    .run() itself is therefore untouched by this card: it keeps calling
    `self._dependencies.execute(...)` at exactly the same call site as
    before, and simply gets a write-ahead-aware callable when one is wired
    in by the bundle -- so `execute` is still "bracketed" from run()'s
    point of view without giving the orchestrator a `_graph_port` attribute
    it has never held.

    `thread_id` is read from `state.active_investigation_anchor["thread_id"]`
    at call time (the field A202's `run_annatar_cycle` establishes and
    maintains across cycles) -- `state` is passed into `execute` fresh each
    cycle, so this always reflects whatever thread was active as of the end
    of the *previous* cycle's `reason` phase (or None on the very first
    cycle / whenever no investigation thread is currently open), which is
    the correct, intended semantics per A202.
    """

    def _wrapped(state: WorkflowState, perception: Any, goal: Any, vet: Any) -> PhaseResult[Any]:
        cycle_id = None
        anchor = state.active_investigation_anchor
        thread_id = anchor.get("thread_id") if anchor is not None else None
        if graph_port is not None and thread_id is not None:
            write_cycle = getattr(graph_port, "write_cycle", None)
            if write_cycle is not None:
                try:
                    write_result = write_cycle(thread_id, state.step_index, action_sent=True)
                    cycle_id = write_result.get("cycle_id") if isinstance(write_result, dict) else None
                except Exception:
                    cycle_id = None  # graph unreachable -- degrade, never block the real action

        result = execute(state, perception, goal, vet)

        if cycle_id is not None:
            confirm_cycle = getattr(graph_port, "confirm_cycle", None)
            if confirm_cycle is not None:
                try:
                    confirm_cycle(cycle_id, decision="pending", confirmed=True)
                except Exception:
                    pass  # a failed confirm write must not crash a successful execution

        return result

    return _wrapped


@dataclass(slots=True)
class WorkflowLimits:
    max_cycles: int = 10
    max_replan_passes_per_cycle: int = 1
    max_consecutive_no_progress: int = 4


class WorkflowOrchestrator:
    """Route ARC v2 phases and apply only orchestration guards."""

    def __init__(self, dependencies: WorkflowDependencies, *, limits: WorkflowLimits | None = None) -> None:
        self._dependencies = dependencies
        self._limits = limits or WorkflowLimits()

    def run(self, state: WorkflowState, observation: Mapping[str, Any]) -> WorkflowRunResult:
        phase_results: list[PhaseResult[Any]] = []
        current_observation: Mapping[str, Any] = observation

        while True:
            budget_reason = check_budget(state.step_index, self._limits.max_cycles)
            if budget_reason is not None:
                budget_result = self._route_budget_through_annatar(state, current_observation, phase_results)
                if budget_result is not None:
                    return budget_result

            cycle_vetoes = 0
            try:
                perception = self._invoke_phase("perceive", self._dependencies.perceive, state, current_observation)
                phase_results.append(perception)
                perception_payload = self._require_payload(perception, WorkflowPhase.PERCEIVE)
                state.previous_grid_hash = perception_payload.grid_hash
                state.loop_history.append(perception_payload.grid_hash)
                state.loop_history_pointer = len(state.loop_history) - 1

                if self._dependencies.readiness_gate is not None and not state.readiness_gate_resolved:
                    readiness_result = self._invoke_phase(
                        "readiness_gate", self._dependencies.readiness_gate, state, perception_payload
                    )
                    phase_results.append(readiness_result)
                    readiness_payload = self._require_payload(readiness_result, WorkflowPhase.READINESS_GATE)
                    readiness_status = readiness_payload.get("status")
                    state.readiness_gate_entities_mapped = readiness_payload.get("entities_mapped")
                    state.readiness_gate_entities_total = readiness_payload.get("entities_total")
                    state.readiness_gate_partial = readiness_status == ReadinessStatus.PARTIAL_FALLTHROUGH

                    probe_candidate = (
                        readiness_payload.get("probe_candidate")
                        if readiness_status == ReadinessStatus.NOT_READY
                        else None
                    )
                    if probe_candidate is not None:
                        # A224 Task 5: the dedicated, deterministic probe path
                        # -- skips resolve/plan/vet entirely (no LLM
                        # escalation, no RETRY/deepening-bias machinery,
                        # which is tuned for an already-anchored
                        # investigation and would prematurely abandon
                        # anchors during broad initial mapping). A synthetic
                        # goal/vet pair wraps the probe candidate so it
                        # reaches execute/evaluate through their existing,
                        # unmodified signatures -- confirmed by direct read
                        # that neither callable's real implementation
                        # branches on `goal` beyond a metadata `goal_id`
                        # field (evaluator.py) or not at all (bundle.py's
                        # execute wrapper).
                        probe_goal = ResolvedGoal(
                            selected=GoalHypothesis(
                                goal_id="readiness_probe",
                                description="Cynefin readiness probe",
                                confidence=0.0,
                            ),
                        )
                        state.active_goal = probe_goal
                        probe_vet = VetDecision(approved=True, candidate=probe_candidate)

                        execution = self._invoke_phase(
                            "execute", self._dependencies.execute, state, perception_payload, probe_goal, probe_vet
                        )
                        phase_results.append(execution)
                        execution_payload = self._require_payload(execution, WorkflowPhase.EXECUTE)

                        evaluation = self._invoke_phase(
                            "evaluate",
                            self._dependencies.evaluate,
                            state,
                            perception_payload,
                            probe_goal,
                            execution_payload,
                        )
                        phase_results.append(evaluation)
                        evaluation_payload = self._require_payload(evaluation, WorkflowPhase.EVALUATE)

                        self._record_execution_attempt(state, execution_payload)
                        self._record_evaluation_state(state, execution_payload, evaluation_payload)
                        state.step_index += 1

                        termination = termination_from_evaluation(evaluation_payload.decision, evaluation_payload.reason)
                        if evaluation.status == PhaseStatus.TERMINATE or termination is not None:
                            return self._finish(
                                state,
                                WorkflowStatus.TERMINATED,
                                evaluation_payload.reason or "terminated",
                                phase_results,
                            )

                        # A230: route every probe cycle through the SAME
                        # self._dependencies.annatar(...) call site the
                        # normal path already uses (below), instead of
                        # `continue`ing without Annatar ever seeing this
                        # cycle happen (the gap this card fixes). The
                        # readiness-gate's own report (status/entities_
                        # mapped/entities_total, already computed above,
                        # unchanged) is passed through so Annatar's signal
                        # layer can read it -- readiness_status()'s own
                        # classification logic is untouched; only who acts
                        # on it changes.
                        if self._dependencies.annatar is not None:
                            outcome = self._dependencies.annatar(
                                state,
                                perception_payload,
                                execution_payload,
                                evaluation_payload,
                                readiness_report=readiness_payload,
                            )
                            state.annatar_degraded = outcome.degraded
                            # A230 live-verification hook: matches the
                            # existing STALL_CHECK precedent below -- a
                            # greppable, concrete record that Annatar was
                            # actually invoked for this probe cycle and what
                            # it decided, since (unlike every other phase)
                            # the annatar dependency is not wrapped by
                            # telemetry.wrap_phase and so never produces a
                            # phase_transition snapshot of its own.
                            import logging as _probe_logging
                            _probe_logging.getLogger(__name__).info(
                                "PROBE_ANNATAR decision=%s exploration_complete=%s entities_mapped=%s entities_total=%s",
                                outcome.decision,
                                outcome.exploration_complete,
                                state.readiness_gate_entities_mapped,
                                state.readiness_gate_entities_total,
                            )
                            if outcome.decision == "terminate":
                                return self._finish(state, WorkflowStatus.TERMINATED, "annatar_exhausted", phase_results)

                            current_observation = execution_payload.observation
                            if outcome.exploration_complete is True:
                                # Annatar's own outcome -- not readiness_
                                # status()'s raw return value -- says the
                                # world model is sufficiently explored. Fall
                                # through (no `continue`) into the same
                                # cycle's normal resolve/plan/vet path
                                # immediately below, instead of burning a
                                # whole extra cycle just to notice the gate
                                # resolved. The "stop gating" code just below
                                # still runs and sets
                                # state.readiness_gate_resolved = True --
                                # harmlessly idempotent when already True.
                                pass
                            else:
                                # False (still not ready) or None (Annatar
                                # configured but produced no readiness
                                # opinion this cycle -- treat conservatively,
                                # same as False): keep probing next cycle.
                                continue
                        else:
                            # No Annatar configured -- byte-for-byte pre-A230
                            # behavior, same backward-compatibility
                            # convention every other
                            # `if self._dependencies.annatar is not None:`
                            # branch in this file already provides: branch
                            # directly on readiness_status()'s own return
                            # value (already done above via probe_candidate),
                            # always continue probing next cycle.
                            current_observation = execution_payload.observation
                            continue

                    # NOT_READY with nothing left to probe this cycle,
                    # READY/PARTIAL_FALLTHROUGH, or Annatar just reported
                    # exploration_complete=True above: stop gating and
                    # proceed through the normal path from here on.
                    # Re-invoking the gate (and its graph_port calls) every
                    # remaining cycle would just repeat the same check for
                    # no benefit.
                    state.readiness_gate_resolved = True

                resolved_goal = self._invoke_phase("resolve", self._dependencies.resolve, state, perception_payload)
                phase_results.append(resolved_goal)
                resolved_goal_payload = self._require_payload(resolved_goal, WorkflowPhase.RESOLVE)
                state.active_goal = resolved_goal_payload

                planning = self._invoke_phase(
                    "plan",
                    self._dependencies.plan,
                    state,
                    perception_payload,
                    resolved_goal_payload,
                )
                phase_results.append(planning)
                planning_payload = self._require_payload(planning, WorkflowPhase.PLAN)

                vet = self._invoke_phase(
                    "vet",
                    self._dependencies.vet,
                    state,
                    perception_payload,
                    resolved_goal_payload,
                    planning_payload,
                )
                phase_results.append(vet)
                vet_payload = self._require_payload(vet, WorkflowPhase.VET)
                if not vet_payload.approved or vet.status == PhaseStatus.VETO:
                    cycle_vetoes += 1
                    state.latest_veto_reason = vet_payload.reason or vet.reason
                    state.latest_veto_alternative = vet_payload.alternative or vet_payload.candidate
                    state.replan_passes += 1
                    if cycle_vetoes > self._limits.max_replan_passes_per_cycle:
                        veto_result = self._route_second_veto_through_annatar(state, perception_payload, current_observation, phase_results)
                        if veto_result is not None:
                            return veto_result
                        continue

                    resolved_goal = self._invoke_phase("resolve", self._dependencies.resolve, state, perception_payload)
                    phase_results.append(resolved_goal)
                    resolved_goal_payload = self._require_payload(resolved_goal, WorkflowPhase.RESOLVE)
                    state.active_goal = resolved_goal_payload

                    planning = self._invoke_phase(
                        "plan",
                        self._dependencies.plan,
                        state,
                        perception_payload,
                        resolved_goal_payload,
                    )
                    phase_results.append(planning)
                    planning_payload = self._require_payload(planning, WorkflowPhase.PLAN)

                    vet = self._invoke_phase(
                        "vet",
                        self._dependencies.vet,
                        state,
                        perception_payload,
                        resolved_goal_payload,
                        planning_payload,
                    )
                    phase_results.append(vet)
                    vet_payload = self._require_payload(vet, WorkflowPhase.VET)
                    if not vet_payload.approved or vet.status == PhaseStatus.VETO:
                        state.latest_veto_reason = vet_payload.reason or vet.reason
                        state.latest_veto_alternative = vet_payload.alternative or vet_payload.candidate
                        veto_result = self._route_second_veto_through_annatar(state, perception_payload, current_observation, phase_results)
                        if veto_result is not None:
                            return veto_result
                        continue

                execution = self._invoke_phase(
                    "execute",
                    self._dependencies.execute,
                    state,
                    perception_payload,
                    resolved_goal_payload,
                    vet_payload,
                )
                phase_results.append(execution)
                execution_payload = self._require_payload(execution, WorkflowPhase.EXECUTE)

                evaluation = self._invoke_phase(
                    "evaluate",
                    self._dependencies.evaluate,
                    state,
                    perception_payload,
                    resolved_goal_payload,
                    execution_payload,
                )
                phase_results.append(evaluation)
                evaluation_payload = self._require_payload(evaluation, WorkflowPhase.EVALUATE)

                self._record_execution_attempt(state, execution_payload)
                self._record_evaluation_state(state, execution_payload, evaluation_payload)
                state.step_index += 1

                # A202 / spec section 5: the environment's own authoritative
                # terminal signal (a real win/loss from the ARC API itself,
                # via termination_from_evaluation) stays independent and
                # short-circuits *before* Annatar runs -- "an
                # environment-terminal result doesn't need a strategic
                # opinion." This check is deliberately positioned ahead of
                # the stall/Annatar block below (moved up from its prior
                # position after the stall check) so a real terminal result
                # is never routed through Annatar logic, and Annatar
                # is never invoked once the episode is already over.
                termination = termination_from_evaluation(evaluation_payload.decision, evaluation_payload.reason)
                if evaluation.status == PhaseStatus.TERMINATE or termination is not None:
                    return self._finish(state, WorkflowStatus.TERMINATED, evaluation_payload.reason or "terminated", phase_results)

                available_actions = current_observation.get("available_actions", [])
                num_available = len(available_actions)
                # Count distinct base actions so ACTION6@x,y click targets don't
                # inflate the attempted count past the available action space.
                num_attempted = count_base_actions(state.action_attempt_counts)
                untested_remaining = (num_available or 1) - num_attempted
                import logging as _logging
                _logging.getLogger(__name__).info(
                    "STALL_CHECK no_progress=%d, available=%d, attempted=%d, untested=%d, threshold=%d",
                    state.consecutive_no_progress_count,
                    num_available or 1,
                    num_attempted,
                    untested_remaining,
                    stall_threshold(self._limits.max_consecutive_no_progress, num_available),
                )
                stall_reason = check_stall(
                    state.consecutive_no_progress_count,
                    self._limits.max_consecutive_no_progress,
                    num_available,
                    num_attempted,
                )

                if self._dependencies.annatar is not None:
                    # A202 / spec section 5: the Annatar now owns the
                    # advance/repeat/terminate decision. check_stall's signal
                    # is folded in as one of its inputs (see
                    # annatar_signals.compute_cycle_signals) instead of
                    # independently ending the run in the `else` branch below.
                    #
                    # A212 (visibility-only, audit conclusion): if this
                    # cycle's first plan_vetter rejection was resolved by the
                    # local same-cycle retry (cycle_vetoes > 0 but we still
                    # reached here -- a second veto would have routed through
                    # _route_second_veto_through_annatar and `continue`d
                    # instead), pass its reason/alternative along too.
                    # Gating on the local `cycle_vetoes` counter (not
                    # directly reading state.latest_veto_reason) matters:
                    # that state field is never reset, so reading it
                    # unconditionally would leak a stale veto from an earlier
                    # cycle into a cycle that had no veto at all. Purely
                    # informational -- see CycleSignals.veto_reason's
                    # docstring for why this cannot change what happens next.
                    veto_reason = state.latest_veto_reason if cycle_vetoes else None
                    veto_alternative_action_id = (
                        state.latest_veto_alternative.action_id
                        if cycle_vetoes and state.latest_veto_alternative is not None
                        else None
                    )
                    # A234: resolved_goal_payload is already in scope (set a
                    # few lines above, right after the last `resolve` call
                    # this cycle -- either the first resolve at the top of
                    # the cycle, or the local same-cycle retry's resolve if
                    # a first veto triggered one). Fold goal_resolver.py::
                    # resolve()'s own already-computed per-cycle output into
                    # this same existing Annatar call, mirroring A230's
                    # readiness_report -- no new call site, no new routing
                    # logic. `top_two_confidence_gap` re-derives the same
                    # ambiguity measure goal_resolver.py::_should_escalate_
                    # to_llm computes internally (selected vs. top
                    # alternative's confidence), read-only here from data
                    # already sitting on ResolvedGoal -- None when there is
                    # no alternative to compare against.
                    resolve_report = {
                        "grounding_gate_passed": resolved_goal_payload.grounding_gate_passed,
                        "llm_escalated": bool(resolved_goal_payload.metadata.get("llm_escalated")),
                        "llm_reason": resolved_goal_payload.metadata.get("llm_reason"),
                        "hypothesis_count": len(resolved_goal_payload.metadata.get("hypotheses", [])),
                        "top_two_confidence_gap": (
                            resolved_goal_payload.selected.confidence - resolved_goal_payload.alternatives[0].confidence
                            if resolved_goal_payload.alternatives
                            else None
                        ),
                    }
                    outcome = self._dependencies.annatar(
                        state,
                        perception_payload,
                        execution_payload,
                        evaluation_payload,
                        stall_reason=stall_reason,
                        veto_reason=veto_reason,
                        veto_alternative_action_id=veto_alternative_action_id,
                        resolve_report=resolve_report,
                    )
                    # A234 live-verification hook: mirrors A230's own
                    # PROBE_ANNATAR precedent -- a greppable, concrete
                    # record that resolve()'s real output actually reached
                    # this call, since (unlike every other phase) the
                    # annatar dependency is not wrapped by
                    # telemetry.wrap_phase and so never produces a
                    # phase_transition snapshot of its own.
                    import logging as _resolve_logging
                    _resolve_logging.getLogger(__name__).info(
                        "RESOLVE_ANNATAR grounding_gate_passed=%s llm_escalated=%s top_two_confidence_gap=%s",
                        resolve_report["grounding_gate_passed"],
                        resolve_report["llm_escalated"],
                        resolve_report["top_two_confidence_gap"],
                    )
                    # A205 / spec section 8: make a degraded (graph-unreachable)
                    # Annatar cycle visible in telemetry rather than silently
                    # swallowed. Set every cycle the Annatar actually runs, so
                    # this always reflects the most recent cycle's outcome.
                    state.annatar_degraded = outcome.degraded
                    if outcome.decision == "terminate":
                        return self._finish(state, WorkflowStatus.TERMINATED, "annatar_exhausted", phase_results)
                    if outcome.decision in ("repeat_deepen", "repeat_retry"):
                        # Consumed by a later card (A203, anchor-biasing in
                        # goal_resolver/plan_generator) -- this card only
                        # needs to produce and store the hint correctly.
                        state.annatar_anchor_hint = outcome
                    else:
                        state.annatar_anchor_hint = None
                else:
                    # No Annatar configured -- today's exact existing
                    # behavior, byte-for-byte.
                    if stall_reason is not None:
                        return self._finish(state, WorkflowStatus.STALLED, stall_reason, phase_results)

                current_observation = execution_payload.observation
            except Exception:
                # A211: best-effort close-out of investigation thread on crash,
                # without ever risking the original crash's traceback being masked.
                traceback_text = traceback.format_exc()
                anchor = state.active_investigation_anchor
                thread_id = anchor.get("thread_id") if isinstance(anchor, dict) else None
                if (
                    self._dependencies.annatar is not None
                    and thread_id is not None
                    and self._dependencies.on_crash_cleanup is not None
                ):
                    try:
                        # Close out the thread's graph state before returning CRASHED.
                        # State value "exhausted" is used as an interim fit for the crash
                        # close-out case, per A211's analysis that the existing state enum
                        # has no perfect semantic match for "abnormally terminated."
                        self._dependencies.on_crash_cleanup(thread_id, "exhausted")
                    except Exception:
                        # A211 non-negotiable: cleanup failure must never mask or replace
                        # the original crash's traceback. The cleanup exception is silently
                        # swallowed here; only the original traceback is reported.
                        pass
                return self._finish(
                    state,
                    WorkflowStatus.CRASHED,
                    "crash",
                    phase_results,
                    traceback_text=traceback_text,
                )

    def _route_budget_through_annatar(
        self,
        state: WorkflowState,
        current_observation: Mapping[str, Any],
        phase_results: list[PhaseResult[Any]],
    ) -> WorkflowRunResult:
        """A209 fix (2026-08-25): `check_budget` previously ended the episode
        directly, without ever giving the Annatar a say -- another place the
        "one agent that sees everything end-to-end" was structurally excluded
        from a termination decision. Unlike `second_veto`, `check_budget` fires
        BEFORE perceive runs, so there is no perception payload for the current
        cycle at all.

        Strategy: On the first iteration (step_index=0), there are no prior
        cycles, so we skip the Annatar and end directly -- nothing to report.
        On subsequent iterations, we give the Annatar a synthetic "budget
        exhausted" signal (fresh, empty payloads -- A209's plan called this
        "Option A"; its own Outcome section wrote "Option B" by mistake,
        which described reusing the prior cycle's real state instead --
        that is NOT what this implements, correct the record if referencing
        it later) with the current (unchanged) observation, so it can close
        out its own bookkeeping (e.g. write_thread_state on an open
        investigation thread) before the episode ends.

        Every branch of this method returns a real WorkflowRunResult -- there
        is no path that returns None. In particular, `outcome.decision` from
        the Annatar call below is deliberately never inspected: the budget
        ceiling is non-negotiable regardless of what the Annatar decides, so
        the method doesn't depend on (and doesn't assert) the Annatar
        producing any particular decision. This is what makes the hard-ceiling
        guarantee structural rather than a matter of trusting the Annatar to
        answer correctly -- see test_annatar_response_does_not_override_budget
        for the proof (a Annatar mock returning "advance" still ends the
        episode as BUDGET_EXHAUSTED with zero further phases invoked)."""
        # If no Annatar configured, behavior is byte-for-byte identical to before.
        if self._dependencies.annatar is None:
            return self._finish(state, WorkflowStatus.BUDGET_EXHAUSTED, "budget_exhausted", phase_results)

        # First iteration has no prior cycle to report; end immediately.
        if state.step_index == 0:
            return self._finish(state, WorkflowStatus.BUDGET_EXHAUSTED, "budget_exhausted", phase_results)

        # For step_index > 0, construct synthetic payloads representing
        # "budget exhausted before we could run any phases." Annatar sees
        # the last known observation and decides to terminate (as it should,
        # since the budget is non-negotiable).
        synthetic_execution = ExecutionResult(
            action_id="", candidate=None, observation=current_observation, metadata={}
        )
        synthetic_evaluation = EvaluationResult(
            decision=WorkflowDecision.CONTINUE,
            meaningful_progress=False,
            reason="budget_exhausted",
            metadata={"grid_changed": False},
        )

        # We don't have a perception payload from this cycle (it hasn't run yet).
        # Annatar needs one for its signature. We can't easily get the prior
        # cycle's perception payload from the function signature, so we create a
        # minimal synthetic one. This is acceptable because Annatar's only
        # real decision here is to TERMINATE (the budget is hard), so the
        # perception details don't matter — the signal "budget_exhausted" overrides.
        # In a future iteration, if we store perception payloads in state, we
        # could reuse the last real one instead.
        from .types import PerceptionSnapshot  # Local import to avoid circular deps

        synthetic_perception = PerceptionSnapshot(
            grid_hash="",
            observation=current_observation,
            grid_shape=None,
            loop_signal=False,
            repeated_grid_count=0,
            entities=(),
            metadata={"synthetic": True, "reason": "budget_exhausted"},
        )

        outcome = self._dependencies.annatar(
            state,
            synthetic_perception,
            synthetic_execution,
            synthetic_evaluation,
            stall_reason="budget_exhausted",
        )
        # The Annatar's decision should be to terminate (the budget is hard).
        # But even if it somehow said "continue", we end the episode anyway
        # because the budget is non-negotiable.
        return self._finish(state, WorkflowStatus.BUDGET_EXHAUSTED, "budget_exhausted", phase_results)

    def _route_second_veto_through_annatar(
        self,
        state: WorkflowState,
        perception_payload: Any,
        current_observation: Mapping[str, Any],
        phase_results: list[PhaseResult[Any]],
    ) -> WorkflowRunResult | None:
        """Post-A206 fix (2026-08-25): a double veto previously ended the
        episode directly, without ever giving the Annatar a say -- the one
        place the "one agent that sees everything end-to-end" was
        structurally excluded from a real strategic decision ("the safety
        layer just rejected our plan twice, what now"). `vet` fires before
        `execute`/`evaluate`, so there's no real ExecutionResult/
        EvaluationResult for this cycle -- a synthetic "nothing was
        attempted" pair is fed to the Annatar instead
        (execution.candidate=None, meaningful_progress=False), plus
        stall_reason="second_veto" (reusing the existing stall-fold
        mechanism from compute_cycle_signals rather than inventing a
        parallel one). A repeated-double-veto pathological case is bounded
        by the same whole-episode-futility streak
        (annatar_signals.run_annatar_cycle) built alongside this fix --
        no new termination logic needed for that case specifically.

        Returns a WorkflowRunResult if the episode should end now (no
        Annatar configured -- exact prior behavior -- or the Annatar said
        "terminate"); None if the caller should `continue` the outer loop
        instead, letting the Annatar's decision (a fresh anchor, or the
        same one) drive the next cycle."""
        if self._dependencies.annatar is None:
            return self._finish(state, WorkflowStatus.SKIPPED, "second_veto", phase_results)

        synthetic_execution = ExecutionResult(action_id="", candidate=None, observation=current_observation, metadata={})
        synthetic_evaluation = EvaluationResult(
            decision=WorkflowDecision.CONTINUE,
            meaningful_progress=False,
            reason="second_veto",
            metadata={"grid_changed": False},
        )
        outcome = self._dependencies.annatar(
            state,
            perception_payload,
            synthetic_execution,
            synthetic_evaluation,
            stall_reason="second_veto",
        )
        if outcome.decision == "terminate":
            return self._finish(state, WorkflowStatus.TERMINATED, "annatar_exhausted", phase_results)
        state.annatar_anchor_hint = outcome if outcome.decision in ("repeat_deepen", "repeat_retry") else None
        return None

    def _invoke_phase(self, name: str, phase_callable: Any, *args: Any) -> PhaseResult[Any]:
        result = phase_callable(*args)
        if not isinstance(result, PhaseResult):
            raise TypeError(f"{name} phase must return PhaseResult")
        return result

    @staticmethod
    def _require_payload(result: PhaseResult[Any], phase: WorkflowPhase) -> Any:
        if result.payload is None:
            raise ValueError(f"{phase.value} phase returned no payload")
        return result.payload

    @staticmethod
    def _record_execution_attempt(state: WorkflowState, execution: ExecutionResult) -> None:
        action_key = execution.candidate.book_id if execution.candidate is not None else execution.action_id
        state.action_attempt_counts[action_key] = state.action_attempt_counts.get(action_key, 0) + 1

    @staticmethod
    def _record_evaluation_state(
        state: WorkflowState,
        execution: ExecutionResult,
        evaluation: EvaluationResult,
    ) -> None:
        action_key = execution.candidate.book_id if execution.candidate is not None else execution.action_id
        state.consecutive_no_progress_count = record_evaluation_outcome(
            no_progress_count=state.consecutive_no_progress_count,
            falsification_counts=state.action_falsification_counts,
            action_key=action_key,
            meaningful_progress=evaluation.meaningful_progress,
            falsification_delta=evaluation.falsification_delta,
        )
        if state.active_goal is not None:
            goal_id = state.active_goal.selected.goal_id
            if evaluation.meaningful_progress:
                state.goal_failure_counts[goal_id] = 0
            else:
                state.goal_failure_counts[goal_id] = state.goal_failure_counts.get(goal_id, 0) + 1

    @staticmethod
    def _finish(
        state: WorkflowState,
        status: WorkflowStatus,
        reason: str,
        phase_results: list[PhaseResult[Any]],
        *,
        traceback_text: str | None = None,
    ) -> WorkflowRunResult:
        state.terminated = True
        state.termination_state = status
        state.crash_traceback = traceback_text
        return WorkflowRunResult(
            status=status,
            state=state,
            phase_results=phase_results,
            reason=reason,
            traceback=traceback_text,
            completed_cycles=state.step_index,
        )
