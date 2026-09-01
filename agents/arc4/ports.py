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
    # A231: optional available_actions param -- see graph_queries.py::
    # ArcGraphQueryPort.fetch_untested_actions for why it's needed for the
    # signal to be non-vacuous.
    def fetch_untested_actions(self, available_actions: Sequence[str] | None = None) -> list[str]: ...

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
    site only ever needs to pass stall_reason explicitly.

    `veto_reason`/`veto_alternative_action_id` (A212, visibility-only): set
    only when this cycle's first plan_vetter rejection was resolved by the
    local same-cycle resolve/plan/vet retry (i.e. the cycle reached
    execute/evaluate/annatar normally rather than routing through
    `_route_second_veto_through_annatar`). Purely informational -- folded
    into CycleSignals but never read by transition()/decision_for_state(),
    so it carries no decision authority, matching the `check_budget`
    (A209) informed-not-empowered precedent rather than `second_veto`'s
    (A207) full escalation.

    `readiness_report` (A230, same optional-keyword pattern as
    `stall_reason`): the Cynefin readiness gate's own report
    (`status`/`entities_mapped`/`entities_total`), passed through by
    workflow.py's probe-path block on every probe cycle so Annatar's own
    outcome -- not workflow.py's direct read of `readiness_status()` --
    drives whether probing continues. See annatar_signals.run_annatar_cycle
    and AnnatarOutcome.exploration_complete.

    `resolve_report` (A234, same optional-keyword, informational-only
    pattern as `readiness_report`): goal_resolver.py::resolve()'s own
    already-computed per-cycle output (`grounding_gate_passed`/
    `llm_escalated`/hypothesis-ambiguity), passed through by workflow.py's
    normal-cycle call from the `resolved_goal_payload` it already holds.
    Carries no decision weight -- see annatar_signals.run_annatar_cycle's
    own docstring and CycleSignals.resolve_hypothesis_ambiguity for why."""

    def __call__(
        self,
        state: WorkflowState,
        perception: PerceptionSnapshot,
        execution: ExecutionResult,
        evaluation: EvaluationResult,
        *,
        graph_port: GraphQueryPort | None = None,
        stall_reason: str | None = None,
        veto_reason: str | None = None,
        veto_alternative_action_id: str | None = None,
        readiness_report: Mapping[str, Any] | None = None,
        resolve_report: Mapping[str, Any] | None = None,
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
    on_crash_cleanup: callable | None = None  # A211: best-effort thread closure on crash (no-op if None)
    # A224: the Cynefin readiness gate, called right after perceive, before
    # resolve. None means "no readiness gate, run exactly as today" -- same
    # backward-compat convention `annatar` already established.
    # WorkflowOrchestrator itself holds no graph_port (by design, see
    # wrap_execute_with_write_ahead's own docstring) -- this callable is a
    # closure over graph_port captured at bundle-construction time, the same
    # pattern resolve/plan/execute already use. Takes (state, perception),
    # returns PhaseResult with payload {"status": ReadinessStatus,
    # "entity_domains": dict, "entities_mapped": int, "entities_total": int}.
    readiness_gate: callable | None = None
