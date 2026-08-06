"""Injected ARC v2 port protocols."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from .types import (
    EvaluationResult,
    ExecutionResult,
    GoalHypothesis,
    PerceptionSnapshot,
    PhaseResult,
    PlanningResult,
    ResolvedGoal,
    VetDecision,
    WorkflowState,
)


@dataclass(slots=True)
class LLMMessage:
    role: str
    content: str


@runtime_checkable
class GraphQueryPort(Protocol):
    def ingest_perception(self, perception: PerceptionSnapshot) -> Any: ...

    def fetch_goal_evidence(
        self,
        perception: PerceptionSnapshot,
        goal: ResolvedGoal | GoalHypothesis | None = None,
    ) -> Any: ...

    def record_plan(self, plan: PlanningResult) -> Any: ...

    def record_vet(self, vet: VetDecision) -> Any: ...

    def record_execution(self, execution: ExecutionResult) -> Any: ...

    def record_evaluation(self, evaluation: EvaluationResult) -> Any: ...

    # A135: Graph read methods for planner, vet, evaluator
    def fetch_untested_actions(self) -> list[str]: ...

    def fetch_per_action_evidence(self, action_id: str) -> dict[str, Any]: ...

    def check_action_gate(self, action_id: str) -> dict[str, Any]: ...

    def fetch_causal_path(self, action_id: str) -> dict[str, Any]: ...


@runtime_checkable
class LLMPort(Protocol):
    def chat(self, messages: Sequence[LLMMessage]) -> str: ...


@runtime_checkable
class PerceivePhase(Protocol):
    def __call__(self, state: WorkflowState, observation: Mapping[str, Any]) -> PhaseResult[PerceptionSnapshot]: ...


@runtime_checkable
class ResolvePhase(Protocol):
    def __call__(self, state: WorkflowState, perception: PerceptionSnapshot) -> PhaseResult[ResolvedGoal]: ...


@runtime_checkable
class PlanPhase(Protocol):
    def __call__(self, state: WorkflowState, perception: PerceptionSnapshot, goal: ResolvedGoal) -> PhaseResult[PlanningResult]: ...


@runtime_checkable
class VetPhase(Protocol):
    def __call__(
        self,
        state: WorkflowState,
        perception: PerceptionSnapshot,
        goal: ResolvedGoal,
        plan: PlanningResult,
    ) -> PhaseResult[VetDecision]: ...


@runtime_checkable
class ExecutePhase(Protocol):
    def __call__(
        self,
        state: WorkflowState,
        perception: PerceptionSnapshot,
        goal: ResolvedGoal,
        vet: VetDecision,
    ) -> PhaseResult[ExecutionResult]: ...


@runtime_checkable
class EvaluatePhase(Protocol):
    def __call__(
        self,
        state: WorkflowState,
        perception: PerceptionSnapshot,
        goal: ResolvedGoal,
        execution: ExecutionResult,
    ) -> PhaseResult[EvaluationResult]: ...


@dataclass(slots=True)
class WorkflowDependencies:
    perceive: PerceivePhase
    resolve: ResolvePhase
    plan: PlanPhase
    vet: VetPhase
    execute: ExecutePhase
    evaluate: EvaluatePhase
