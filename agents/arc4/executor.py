"""Deterministic ARC v2 execution module."""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import Any, Mapping

from .types import ExecutionResult, PhaseResult, PhaseStatus, PlanCandidate, WorkflowPhase, WorkflowState


@dataclass(slots=True)
class Executor:
    """Execute an approved plan against an injected game transport."""

    transport: Any | None = None

    def __call__(
        self,
        state: WorkflowState,
        plan: PlanCandidate,
        game_context: Mapping[str, Any],
    ) -> PhaseResult[ExecutionResult]:
        return self.execute(state, plan, game_context)

    def execute(
        self,
        state: WorkflowState,
        plan: PlanCandidate,
        game_context: Mapping[str, Any],
    ) -> PhaseResult[ExecutionResult]:
        del state
        action_args = self._plan_args(plan)
        context = dict(game_context)

        if self.transport is None:
            return self._failure(plan, "missing_execution_transport", context)

        try:
            transport_result = self._invoke_transport(plan, action_args, context)
            return self._success(plan, transport_result, context)
        except Exception as exc:
            return self._failure(
                plan,
                str(exc) or type(exc).__name__,
                context,
                exception=exc,
            )

    def _invoke_transport(
        self,
        plan: PlanCandidate,
        action_args: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> Any:
        if hasattr(self.transport, "execute_action"):
            return self.transport.execute_action(plan.action_id, action_args, context)
        if hasattr(self.transport, "run_action"):
            return self.transport.run_action(plan.action_id, action_args, context)
        if hasattr(self.transport, "step"):
            return self.transport.step(plan.action_id, action_args, context)
        if callable(self.transport):
            return self.transport(plan.action_id, action_args, context)
        raise TypeError("transport does not expose an execution method")

    def _success(
        self,
        plan: PlanCandidate,
        transport_result: Any,
        context: Mapping[str, Any],
    ) -> PhaseResult[ExecutionResult]:
        observation, did_progress, actual_effect, metadata = self._normalize_result(transport_result)
        execution = ExecutionResult(
            action_id=plan.action_id,
            candidate=plan,
            observation=observation,
            did_progress=did_progress,
            predicted_effect=plan.expected_effect,
            actual_effect=actual_effect,
            metadata={
                **metadata,
                "game_context": self._compact_context(context),
            },
        )
        return PhaseResult(phase=WorkflowPhase.EXECUTE, payload=execution)

    def _failure(
        self,
        plan: PlanCandidate,
        reason: str,
        context: Mapping[str, Any],
        *,
        exception: Exception | None = None,
    ) -> PhaseResult[ExecutionResult]:
        metadata: dict[str, Any] = {
            "game_context": self._compact_context(context),
            "failure_reason": reason,
        }
        traceback_text = None
        if exception is not None:
            traceback_text = traceback.format_exc()
            metadata.update(
                {
                    "error_type": type(exception).__name__,
                    "error_message": str(exception),
                    "traceback": traceback_text,
                }
            )

        execution = ExecutionResult(
            action_id=plan.action_id,
            candidate=plan,
            observation={},
            did_progress=False,
            predicted_effect=plan.expected_effect,
            actual_effect=None,
            metadata=metadata,
        )
        if traceback_text is not None:
            execution.metadata["traceback"] = traceback_text
        return PhaseResult(phase=WorkflowPhase.EXECUTE, status=PhaseStatus.CRASH, payload=execution, reason=reason)

    @staticmethod
    def _plan_args(plan: PlanCandidate) -> Mapping[str, Any]:
        metadata = plan.metadata if isinstance(plan.metadata, Mapping) else {}
        merged: dict[str, Any] = {}
        for key in ("args", "action_args", "payload"):
            candidate = metadata.get(key)
            if isinstance(candidate, Mapping):
                merged.update(dict(candidate))
                break
        if isinstance(plan.payload, Mapping):
            merged.update(dict(plan.payload))
        return merged

    @staticmethod
    def _normalize_result(result: Any) -> tuple[Mapping[str, Any], bool, str | None, dict[str, Any]]:
        metadata: dict[str, Any] = {"transport_result_type": type(result).__name__}

        if isinstance(result, PhaseResult):
            payload = result.payload
            if isinstance(payload, ExecutionResult):
                return payload.observation, bool(payload.did_progress), payload.actual_effect, {**metadata, "phase_result": result}
            if isinstance(payload, Mapping):
                observation = dict(payload)
                metadata["phase_result"] = result
                return observation, bool(payload.get("did_progress", False)), payload.get("actual_effect") or payload.get("effect"), metadata

        if isinstance(result, Mapping):
            observation_value = result.get("observation", result)
            observation = dict(observation_value) if isinstance(observation_value, Mapping) else {"value": observation_value}
            did_progress = bool(result.get("did_progress", result.get("progress", False)))
            actual_effect = result.get("actual_effect") or result.get("effect") or result.get("apparent_effect")
            metadata.update({key: value for key, value in result.items() if key != "observation"})
            return observation, did_progress, actual_effect, metadata

        if isinstance(result, (tuple, list)):
            if len(result) >= 4:
                observation_value, reward, done, guid = result[:4]
                observation = dict(observation_value) if isinstance(observation_value, Mapping) else {"value": observation_value}
                metadata.update({"reward": reward, "done": done, "guid": guid})
                return observation, bool(reward) or bool(done), None, metadata
            if len(result) == 2:
                observation_value, aux = result
                observation = dict(observation_value) if isinstance(observation_value, Mapping) else {"value": observation_value}
                metadata["transport_aux"] = aux
                return observation, bool(aux), None, metadata

        observation = result if isinstance(result, Mapping) else {"value": result}
        return dict(observation), False, None, metadata

    @staticmethod
    def _compact_context(context: Mapping[str, Any]) -> dict[str, Any]:
        compact: dict[str, Any] = {}
        for key in ("game_id", "guid", "step", "session_id", "state"):
            if key in context:
                compact[key] = context[key]
        return compact