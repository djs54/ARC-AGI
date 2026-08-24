"""Thin ARC v2 workflow orchestrator."""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import Any, Mapping

from .cycle_policy import check_budget, check_stall, count_base_actions, record_evaluation_outcome, stall_threshold, termination_from_evaluation
from .ports import WorkflowDependencies
from .types import (
    EvaluationResult,
    ExecutionResult,
    PhaseResult,
    PhaseStatus,
    ResolvedGoal,
    WorkflowDecision,
    WorkflowPhase,
    WorkflowRunResult,
    WorkflowState,
    WorkflowStatus,
)


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
                return self._finish(state, WorkflowStatus.BUDGET_EXHAUSTED, budget_reason, phase_results)

            cycle_vetoes = 0
            try:
                perception = self._invoke_phase("perceive", self._dependencies.perceive, state, current_observation)
                phase_results.append(perception)
                perception_payload = self._require_payload(perception, WorkflowPhase.PERCEIVE)
                state.previous_grid_hash = perception_payload.grid_hash
                state.loop_history.append(perception_payload.grid_hash)
                state.loop_history_pointer = len(state.loop_history) - 1

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
                        return self._finish(state, WorkflowStatus.SKIPPED, "second_veto", phase_results)

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
                        return self._finish(state, WorkflowStatus.SKIPPED, "second_veto", phase_results)

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
                # short-circuits *before* the Reasoner runs -- "an
                # environment-terminal result doesn't need a strategic
                # opinion." This check is deliberately positioned ahead of
                # the stall/Reasoner block below (moved up from its prior
                # position after the stall check) so a real terminal result
                # is never routed through Reasoner logic, and the Reasoner
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

                if self._dependencies.reason is not None:
                    # A202 / spec section 5: the Reasoner now owns the
                    # advance/repeat/terminate decision. check_stall's signal
                    # is folded in as one of its inputs (see
                    # reasoner_signals.compute_cycle_signals) instead of
                    # independently ending the run in the `else` branch below.
                    outcome = self._dependencies.reason(
                        state,
                        perception_payload,
                        execution_payload,
                        evaluation_payload,
                        stall_reason=stall_reason,
                    )
                    if outcome.decision == "terminate":
                        return self._finish(state, WorkflowStatus.TERMINATED, "reasoner_exhausted", phase_results)
                    if outcome.decision in ("repeat_deepen", "repeat_retry"):
                        # Consumed by a later card (A203, anchor-biasing in
                        # goal_resolver/plan_generator) -- this card only
                        # needs to produce and store the hint correctly.
                        state.reasoner_anchor_hint = outcome
                    else:
                        state.reasoner_anchor_hint = None
                else:
                    # No Reasoner configured -- today's exact existing
                    # behavior, byte-for-byte.
                    if stall_reason is not None:
                        return self._finish(state, WorkflowStatus.STALLED, stall_reason, phase_results)

                current_observation = execution_payload.observation
            except Exception:
                return self._finish(
                    state,
                    WorkflowStatus.CRASHED,
                    "crash",
                    phase_results,
                    traceback_text=traceback.format_exc(),
                )

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
