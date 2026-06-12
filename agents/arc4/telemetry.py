"""Telemetry helpers that project ARC v2 workflow results into smoke artifacts."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .types import EvaluationResult, ExecutionResult, GoalHypothesis, PlanningResult, ResolvedGoal, VetDecision, WorkflowDecision, WorkflowRunResult, WorkflowState, WorkflowStatus
from .evaluator import classify_v2_termination


@dataclass(slots=True)
class ArcV2Telemetry:
    """Collect phase-level snapshots and final run artifacts for one task."""

    task_id: str
    game_id: str
    game_title: str = ""
    game_tags: tuple[str, ...] = ()
    append_snapshot: Callable[[dict[str, Any]], None] | None = None
    world_model_eval: bool = False
    started_at: float = field(default_factory=time.monotonic)
    tokens_input: int = 0
    tokens_output: int = 0
    _phase_history: list[dict[str, Any]] = field(default_factory=list)
    _cycle_index: int = 0
    _last_phase: str = "setup"
    _latest_observation: Mapping[str, Any] | None = None
    _latest_goal: ResolvedGoal | None = None
    _latest_plan: PlanningResult | None = None
    _latest_vet: VetDecision | None = None
    _latest_execution: ExecutionResult | None = None
    _latest_evaluation: EvaluationResult | None = None

    def wrap_phase(self, phase_name: str, phase_callable: Callable[..., Any]) -> Callable[..., Any]:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            result = phase_callable(*args, **kwargs)
            self._record_phase_result(phase_name, result, args)
            return result

        return wrapped

    def _record_phase_result(self, phase_name: str, result: Any, args: tuple[Any, ...]) -> None:
        payload = getattr(result, "payload", None)
        if phase_name == "perceive" and hasattr(payload, "observation"):
            self._latest_observation = getattr(payload, "observation", None)
        elif phase_name == "resolve" and isinstance(payload, ResolvedGoal):
            self._latest_goal = payload
        elif phase_name == "plan" and isinstance(payload, PlanningResult):
            self._latest_plan = payload
        elif phase_name == "vet" and isinstance(payload, VetDecision):
            self._latest_vet = payload
        elif phase_name == "execute" and isinstance(payload, ExecutionResult):
            self._latest_execution = payload
        elif phase_name == "evaluate" and isinstance(payload, EvaluationResult):
            self._latest_evaluation = payload

        snapshot = self._phase_transition_snapshot(phase_name, result, args)
        self._phase_history.append(snapshot)
        self._emit(snapshot)

        if phase_name == "evaluate" and isinstance(payload, EvaluationResult):
            step_snapshot = self._step_snapshot(args)
            self._phase_history.append(step_snapshot)
            self._emit(step_snapshot)
            self._cycle_index += 1
            self._last_phase = phase_name

    def build_final_result(self, workflow_result: WorkflowRunResult) -> dict[str, Any]:
        final_state = self._latest_observation_state() or workflow_result.status.value
        failure_class = classify_v2_termination(workflow_result.status.value, workflow_result.reason or "")
        final_result = {
            "task_id": self.task_id,
            "game_id": self.game_id,
            "game_title": self.game_title,
            "game_tags": list(self.game_tags),
            "correct": self._is_success(workflow_result.status, final_state),
            "steps": workflow_result.state.step_index,
            "runtime_seconds": round(time.monotonic() - self.started_at, 3),
            "failure_class": failure_class,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "cost_usd": round((self.tokens_input + self.tokens_output) * 0.000001, 6),
            "final_state": final_state,
            "reason": workflow_result.reason,
            "traceback": workflow_result.traceback,
            "solve_phase_summary": self._solve_phase_summary(workflow_result.state),
            "world_model_snapshot": self._world_model_snapshot(workflow_result.state),
            "agent_execution_trace": list(self._phase_history),
            "sidequests_ledger": [],
            "arc_server_responses": [],
            "metadata": {
                "workflow_status": workflow_result.status.value,
                "completed_cycles": workflow_result.completed_cycles,
            },
        }
        if self.world_model_eval:
            final_result["world_model_live_snapshot"] = dict(final_result["world_model_snapshot"])
        return final_result

    def _phase_transition_snapshot(self, phase_name: str, result: Any, args: Sequence[Any]) -> dict[str, Any]:
        state = self._extract_state(args)
        payload = getattr(result, "payload", None)
        snapshot = {
            "snapshot_type": "phase_transition",
            "task_id": self.task_id,
            "game_id": self.game_id,
            "game_title": self.game_title,
            "game_tags": list(self.game_tags),
            "current_phase": phase_name,
            "from_phase": self._last_phase,
            "to_phase": phase_name,
            "phase": phase_name,
            "step": self._cycle_index,
            "status": getattr(result, "status", None).value if getattr(result, "status", None) else "ok",
            "reason": getattr(result, "reason", None),
            "runtime_seconds": round(time.monotonic() - self.started_at, 3),
            "workflow_step_index": getattr(state, "step_index", 0) if state is not None else 0,
        }
        if phase_name == "evaluate" and isinstance(payload, EvaluationResult):
            snapshot.update(
                {
                    "decision": payload.decision.value,
                    "meaningful_progress": payload.meaningful_progress,
                    "falsification_delta": payload.falsification_delta,
                    "goal_id": self._goal_id(),
                    "action_id": self._action_id(),
                }
            )
        return snapshot

    def _step_snapshot(self, args: Sequence[Any]) -> dict[str, Any]:
        state = self._extract_state(args)
        perception = self._extract_perception(args)
        goal = self._latest_goal
        plan = self._latest_plan
        vet = self._latest_vet
        execution = self._latest_execution
        evaluation = self._latest_evaluation

        progress_tier = "flat"
        if evaluation is not None and isinstance(evaluation.metadata, Mapping):
            progress_tier = str(evaluation.metadata.get("progress_tier") or "flat")
        progress_class = "level" if progress_tier == "level" else "grid_change" if progress_tier == "grid_change" else "flat"
        progress_reward = 0.0
        if execution is not None and isinstance(execution.metadata, Mapping):
            progress_reward = float(execution.metadata.get("progress_reward", execution.metadata.get("reward", 0.0)) or 0.0)

        snapshot = {
            "snapshot_type": "step",
            "task_id": self.task_id,
            "game_id": self.game_id,
            "game_title": self.game_title,
            "game_tags": list(self.game_tags),
            "step": self._cycle_index + 1,
            "action_id": self._action_id(),
            "goal_id": self._goal_id(),
            "world_model_node_count": self._world_model_node_count(perception, goal, plan),
            "world_model_edge_count": self._world_model_edge_count(goal, plan, execution),
            "world_model_contradiction_count": int(getattr(state, "action_falsification_counts", {}).get(self._action_id() or "", 0)) if state is not None else 0,
            "world_model_demotion_count": 0,
            "reasoning_skip_count": 0,
            "reasoning_escalation_count": 0,
            "mechanic_prior_recall_status": self._mechanic_prior_status(plan, goal),
            "mechanic_prior_count": self._mechanic_prior_count(plan),
            "mechanic_priors_used_count": self._mechanic_priors_used_count(plan),
            "active_goal_hypothesis_id": goal.selected.goal_id if goal is not None else self._goal_id(),
            "active_goal_confidence": goal.selected.confidence if goal is not None else 0.0,
            "selected_candidate_has_prediction": bool(getattr(plan, "candidate", None) and plan.candidate.expected_effect),
            "selected_candidate_prediction_effect_class": getattr(execution, "actual_effect", None) or getattr(getattr(plan, "candidate", None), "expected_effect", None),
            "selected_candidate_prediction_confidence": getattr(getattr(plan, "candidate", None), "score", 0.0) or 0.0,
            "planner_candidate_count": len(getattr(plan, "alternatives", ()) or ()) + (1 if getattr(plan, "candidate", None) else 0),
            "selected_candidate_has_falsification": bool(vet and not vet.approved),
            "reward": progress_reward,
            "progress_reward": progress_reward,
            "meaningful_progress": bool(evaluation.meaningful_progress if evaluation else False),
            "progress_class": progress_class,
            "action_effect_class": getattr(execution, "actual_effect", None) or "unknown",
            "decision_source": "arc_v2",
            "state": self._latest_observation_state(),
        }

        if evaluation is not None:
            snapshot.update(
                {
                    "decision": evaluation.decision.value,
                    "falsification_delta": evaluation.falsification_delta,
                    "failure_class": self._failure_class_from_decision(evaluation.decision),
                    "failure_reason": evaluation.reason,
                }
            )
        if perception is not None:
            snapshot["grid_hash"] = perception.grid_hash
        return snapshot

    def _emit(self, snapshot: dict[str, Any]) -> None:
        if self.append_snapshot is not None:
            self.append_snapshot(snapshot)

    @staticmethod
    def _extract_state(args: Sequence[Any]) -> WorkflowState | None:
        for arg in args:
            if isinstance(arg, WorkflowState):
                return arg
        return None

    def _extract_perception(self, args: Sequence[Any]) -> Any:
        for arg in args:
            if hasattr(arg, "grid_hash") and hasattr(arg, "entities"):
                return arg
        return None

    def _latest_observation_state(self) -> str | None:
        if isinstance(self._latest_observation, Mapping):
            return str(self._latest_observation.get("state") or self._latest_observation.get("result_state") or "") or None
        return None

    def _goal_id(self) -> str | None:
        if self._latest_goal is not None:
            return self._latest_goal.selected.goal_id
        if self._latest_plan is not None and self._latest_plan.candidate is not None:
            return self._latest_plan.candidate.goal_id
        return None

    def _action_id(self) -> str | None:
        if self._latest_execution is not None:
            return self._latest_execution.action_id
        if self._latest_plan is not None and self._latest_plan.candidate is not None:
            return self._latest_plan.candidate.action_id
        if self._latest_vet is not None and self._latest_vet.candidate is not None:
            return self._latest_vet.candidate.action_id
        return None

    def _mechanic_prior_status(self, plan: PlanningResult | None, goal: ResolvedGoal | None) -> str:
        if plan is None and goal is None:
            return "not_called"
        if plan is not None and plan.metadata.get("graph_records"):
            return "prior_used"
        return "zero_priors"

    def _mechanic_prior_count(self, plan: PlanningResult | None) -> int:
        if plan is not None and isinstance(plan.metadata.get("graph_records"), Sequence):
            return len(plan.metadata.get("graph_records") or [])
        return 0

    def _mechanic_priors_used_count(self, plan: PlanningResult | None) -> int:
        """Count candidates whose action_id was sourced from mechanic prior action_set fields."""
        if plan is None or plan.candidate is None:
            return 0
        count = 0
        all_candidates = [plan.candidate] + list(plan.alternatives or ())
        for candidate in all_candidates:
            meta = candidate.metadata or {}
            if meta.get("mechanic_prior_source"):
                count += 1
        return count

    def _world_model_node_count(self, perception: Any, goal: ResolvedGoal | None, plan: PlanningResult | None) -> int:
        perception_count = len(getattr(perception, "entities", ()) or []) if perception is not None else 0
        goal_count = 1 if goal is not None else 0
        alternative_count = len(goal.alternatives) if goal is not None else 0
        plan_count = len(plan.alternatives) if plan is not None else 0
        return perception_count + goal_count + alternative_count + plan_count

    def _world_model_edge_count(self, goal: ResolvedGoal | None, plan: PlanningResult | None, execution: ExecutionResult | None) -> int:
        goal_edges = len(goal.metadata.get("graph_evidence", [])) if goal is not None else 0
        plan_edges = len(plan.metadata.get("graph_records", [])) if plan is not None else 0
        execution_edges = 1 if execution is not None else 0
        return goal_edges + plan_edges + execution_edges

    @staticmethod
    def _solve_phase_summary(state: WorkflowState) -> dict[str, Any]:
        return {
            "active_goal_id": state.active_goal.selected.goal_id if state.active_goal is not None else None,
            "active_goal_confidence": state.active_goal.selected.confidence if state.active_goal is not None else 0.0,
            "replan_passes": state.replan_passes,
            "no_progress_count": state.consecutive_no_progress_count,
            "action_attempt_counts": dict(state.action_attempt_counts),
            "action_falsification_counts": dict(state.action_falsification_counts),
        }

    @staticmethod
    def _world_model_snapshot(state: WorkflowState) -> dict[str, Any]:
        return {
            "node_count": len(state.action_attempt_counts) + len(state.action_falsification_counts),
            "edge_count": sum(state.action_falsification_counts.values()) if state.action_falsification_counts else 0,
            "contradiction_count": sum(state.action_falsification_counts.values()) if state.action_falsification_counts else 0,
            "demotion_count": state.consecutive_no_progress_count,
        }

    @staticmethod
    def _failure_class(status: WorkflowStatus, final_state: str | None) -> str | None:
        # Legacy method kept for backward compatibility
        if status == WorkflowStatus.CRASHED:
            return "crash"
        if status == WorkflowStatus.BUDGET_EXHAUSTED:
            return "budget_exhausted"
        if status == WorkflowStatus.STALLED:
            return "stalled"
        if status == WorkflowStatus.SKIPPED:
            return "veto"
        if final_state and final_state.upper() == "WIN":
            return None
        return None

    @staticmethod
    def _failure_class_from_decision(decision: WorkflowDecision) -> str | None:
        if decision == WorkflowDecision.PIVOT:
            return "pivot"
        if decision == WorkflowDecision.TERMINATE:
            return "terminate"
        return None

    @staticmethod
    def _is_success(status: WorkflowStatus, final_state: str | None) -> bool:
        return status == WorkflowStatus.TERMINATED and str(final_state or "").upper() == "WIN"


__all__ = ["ArcV2Telemetry"]