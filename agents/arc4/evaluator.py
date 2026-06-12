"""Deterministic ARC v2 post-action evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agents.arc3.failure_taxonomy import FailureTaxonomy
from .ports import GraphQueryPort
from .types import EvaluationResult, ExecutionResult, PhaseResult, PhaseStatus, ResolvedGoal, WorkflowDecision, WorkflowPhase, WorkflowState


@dataclass(slots=True)
class EvaluationLimits:
    repeated_falsification_threshold: int = 2
    exhausted_action_attempt_threshold: int = 4
    stale_repeat_threshold: int = 2  # override progress after N prior attempts of the same action


class Evaluator:
    """Compare prediction and outcome, then return the next workflow decision."""

    def __init__(self, graph_query_port: GraphQueryPort | None = None, *, limits: EvaluationLimits | None = None) -> None:
        self._graph_query_port = graph_query_port
        self._limits = limits or EvaluationLimits()

    def __call__(
        self,
        state: WorkflowState,
        perception: Any,
        goal: ResolvedGoal,
        execution: ExecutionResult,
    ) -> PhaseResult[EvaluationResult]:
        return self.evaluate(state, perception, goal, execution)

    def evaluate(
        self,
        state: WorkflowState,
        perception: Any,
        goal: ResolvedGoal,
        execution: ExecutionResult,
    ) -> PhaseResult[EvaluationResult]:
        action_family = self._action_family(execution.action_id)
        current_falsification_count = self._current_count(state, action_family, execution.action_id)
        current_attempt_count = self._current_attempt_count(state, action_family, execution.action_id)
        predicted_effect = self._normalize_text(execution.predicted_effect)
        actual_effect = self._normalize_text(execution.actual_effect)
        effect_match = predicted_effect is not None and actual_effect is not None and predicted_effect == actual_effect
        terminal_reason = self._terminal_reason(execution)
        action_space_exhausted = self._action_space_exhausted(execution, current_attempt_count)
        exec_meta = execution.metadata if isinstance(execution.metadata, Mapping) else {}
        level_gain = int(exec_meta.get("level_gain") or 0)
        meaningful_progress = bool(execution.did_progress)

        # A133: Override stale progress — repeated action with no new information
        # Use action-specific count (not family) so unrelated siblings don't trigger stale
        stale_override = False
        grid_unchanged = self._grid_unchanged(state, perception)
        grid_changed_flag_raw = exec_meta.get("grid_changed")
        if grid_changed_flag_raw is None:
            grid_changed_flag = not grid_unchanged
        else:
            grid_changed_flag = bool(grid_changed_flag_raw)
        action_specific_attempts = state.action_attempt_counts.get(execution.action_id, 0)
        if meaningful_progress and terminal_reason is None:
            if action_specific_attempts >= self._limits.stale_repeat_threshold:
                meaningful_progress = False
                stale_override = True
            elif grid_unchanged:
                meaningful_progress = False
                stale_override = True

        # A135: Graph causal path check — if the graph traces only contradictions, no real progress
        causal_path: dict[str, Any] = {}
        causal_override = False
        if meaningful_progress and terminal_reason is None and self._graph_query_port is not None:
            try:
                causal_path = self._graph_query_port.fetch_causal_path(execution.action_id)
                if causal_path.get("path_exists") and not causal_path.get("supports") and causal_path.get("contradicts"):
                    meaningful_progress = False
                    causal_override = True
            except Exception:
                pass  # graph unavailable — don't override

        decision = WorkflowDecision.CONTINUE
        falsification_delta = 0
        reason = ""

        if terminal_reason is not None:
            decision = WorkflowDecision.TERMINATE
            reason = terminal_reason
        elif action_space_exhausted:
            decision = WorkflowDecision.TERMINATE
            reason = "action_space_exhausted"
        elif meaningful_progress:
            reason = "meaningful_progress"
        elif effect_match:
            reason = "prediction_confirmed_without_progress"
        else:
            if grid_changed_flag:
                falsification_delta = 0
                reason = "effect_without_progress"
            else:
                falsification_delta = 1
                projected_count = current_falsification_count + falsification_delta
                if projected_count >= self._limits.repeated_falsification_threshold:
                    decision = WorkflowDecision.PIVOT
                    reason = "repeated_falsification"
                else:
                    reason = "prediction_falsified"

        if level_gain > 0:
            progress_tier = "level"
        elif not meaningful_progress and grid_changed_flag:
            progress_tier = "grid_change"
        else:
            progress_tier = "flat"

        evaluation = EvaluationResult(
            decision=decision,
            meaningful_progress=meaningful_progress,
            falsification_delta=falsification_delta,
            reason=reason,
            metadata={
                "action_id": execution.action_id,
                "action_family": action_family,
                "predicted_effect": execution.predicted_effect,
                "actual_effect": execution.actual_effect,
                "effect_match": effect_match,
                "current_falsification_count": current_falsification_count,
                "projected_falsification_count": current_falsification_count + falsification_delta,
                "current_attempt_count": current_attempt_count,
                "meaningful_progress": meaningful_progress,
                "terminal_reason": terminal_reason,
                "action_space_exhausted": action_space_exhausted,
                "falsification_pressure_after": 0 if meaningful_progress else current_falsification_count + falsification_delta,
                "goal_id": goal.selected.goal_id,
                "stale_override": stale_override,
                "grid_unchanged": grid_unchanged,
                "grid_changed": grid_changed_flag,
                "level_gain": level_gain,
                "progress_tier": progress_tier,
                "causal_override": causal_override,
                "causal_path": causal_path,
            },
        )

        evaluation.metadata["graph_recording"] = self._record_evaluation(evaluation)
        return PhaseResult(phase=WorkflowPhase.EVALUATE, status=PhaseStatus.TERMINATE if decision == WorkflowDecision.TERMINATE else PhaseStatus.OK, payload=evaluation, reason=reason or None)

    def _record_evaluation(self, evaluation: EvaluationResult) -> str:
        if self._graph_query_port is None:
            return "skipped"

        record = getattr(self._graph_query_port, "record_evaluation", None)
        if record is None:
            return "skipped"

        try:
            record(evaluation)
        except Exception:
            return "failed"
        return "ok"

    @staticmethod
    def _grid_unchanged(state: WorkflowState, perception: Any) -> bool:
        """Check if the grid is unchanged using the perception's own repeat/loop signals."""
        repeated = getattr(perception, "repeated_grid_count", 0)
        loop = getattr(perception, "loop_signal", False)
        return bool(repeated > 0 or loop)

    @staticmethod
    def _action_family(action_id: str) -> str:
        for separator in (":", "-", "_"):
            if separator in action_id:
                return action_id.split(separator, 1)[0]
        return action_id

    @staticmethod
    def _normalize_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        return normalized or None

    @staticmethod
    def _current_count(state: WorkflowState, action_family: str, action_id: str) -> int:
        if action_family in state.action_falsification_counts:
            return state.action_falsification_counts[action_family]
        return state.action_falsification_counts.get(action_id, 0)

    @staticmethod
    def _current_attempt_count(state: WorkflowState, action_family: str, action_id: str) -> int:
        if action_family in state.action_attempt_counts:
            return state.action_attempt_counts[action_family]
        return state.action_attempt_counts.get(action_id, 0)

    @staticmethod
    def _terminal_reason(execution: ExecutionResult) -> str | None:
        metadata = execution.metadata if isinstance(execution.metadata, Mapping) else {}
        for key in ("terminal", "terminated", "done", "game_over", "solved", "victory"):
            if bool(metadata.get(key)):
                return key
        observation = execution.observation if isinstance(execution.observation, Mapping) else {}
        for key in ("terminal", "terminated", "done", "game_over", "solved", "victory"):
            if bool(observation.get(key)):
                return key
        return None

    def _action_space_exhausted(self, execution: ExecutionResult, current_attempt_count: int) -> bool:
        metadata = execution.metadata if isinstance(execution.metadata, Mapping) else {}
        if bool(metadata.get("action_space_exhausted")) or bool(metadata.get("exhausted_action_space")):
            return True
        return current_attempt_count >= self._limits.exhausted_action_attempt_threshold


def classify_v2_termination(status: str, reason: str, exception: BaseException | None = None) -> str:
    """Map v2 WorkflowStatus to v1 FailureTaxonomy values."""
    if exception is not None:
        from agents.arc3.failure_taxonomy import classify_failure
        return classify_failure(exception).value

    mapping = {
        "stalled": FailureTaxonomy.STRATEGY_EXHAUSTED.value,
        "stall_detected": FailureTaxonomy.STRATEGY_EXHAUSTED.value,
        "budget_exhausted": FailureTaxonomy.WALL_CLOCK_TIMEOUT.value,
        "crash": FailureTaxonomy.CRASH.value,
        "crash_in_perceive": FailureTaxonomy.CRASH.value,
        "crash_in_resolve": FailureTaxonomy.CRASH.value,
        "crash_in_plan": FailureTaxonomy.CRASH.value,
        "crash_in_vet": FailureTaxonomy.CRASH.value,
        "crash_in_execute": FailureTaxonomy.CRASH.value,
        "crash_in_evaluate": FailureTaxonomy.CRASH.value,
        "completed": None,
        "won": None,
    }
    return mapping.get(reason, mapping.get(status, FailureTaxonomy.CRASH.value))