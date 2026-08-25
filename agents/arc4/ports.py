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
    AnnatarOutcome,
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


@runtime_checkable
class AnnatarPhase(Protocol):
    """A202: runs once per cycle, after `evaluate`, and decides whether to
    advance to a new investigation anchor, repeat (deepen or retry), or
    terminate the whole episode. `graph_port`/`stall_reason` are keyword-only
    and optional -- a concrete implementation (agents/arc4/annatar_signals.py
    ::run_annatar_cycle) needs graph access and the orchestrator's own
    check_stall signal, but WorkflowOrchestrator itself does not need to hold
    a graph_port reference: bundle.py wires a closure over graph_port the
    same way it already does for resolve/plan, so the orchestrator's call
    site only ever needs to pass stall_reason explicitly."""

    def __call__(
        self,
        state: WorkflowState,
        perception: PerceptionSnapshot,
        execution: ExecutionResult,
        evaluation: EvaluationResult,
        *,
        graph_port: GraphQueryPort | None = None,
        stall_reason: str | None = None,
    ) -> AnnatarOutcome: ...


@dataclass(slots=True)
class WorkflowDependencies:
    perceive: PerceivePhase
    resolve: ResolvePhase
    plan: PlanPhase
    vet: VetPhase
    execute: ExecutePhase
    evaluate: EvaluatePhase
    annatar: AnnatarPhase | None = None  # None means "no Annatar, run exactly as today"
