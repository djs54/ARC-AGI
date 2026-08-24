"""Shared ARC v2 dataclasses and enums."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Generic, Mapping, TypeVar


class WorkflowPhase(StrEnum):
    PERCEIVE = "perceive"
    RESOLVE = "resolve"
    PLAN = "plan"
    VET = "vet"
    EXECUTE = "execute"
    EVALUATE = "evaluate"


class PhaseStatus(StrEnum):
    OK = "ok"
    VETO = "veto"
    TERMINATE = "terminate"
    CRASH = "crash"


class WorkflowDecision(StrEnum):
    CONTINUE = "continue"
    PIVOT = "pivot"
    TERMINATE = "terminate"


class WorkflowStatus(StrEnum):
    RUNNING = "running"
    SKIPPED = "skipped"
    TERMINATED = "terminated"
    CRASHED = "crashed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    STALLED = "stalled"


@dataclass(slots=True)
class PerceivedEntity:
    kind: str
    value: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "value": self.value,
            "attributes": self.attributes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PerceivedEntity:
        return cls(
            kind=d["kind"],
            value=d["value"],
            attributes=d.get("attributes", {}),
        )


@dataclass(slots=True)
class PerceptionSnapshot:
    observation: Mapping[str, Any]
    grid_hash: str
    grid_shape: tuple[int, int] | None = None
    loop_signal: bool = False
    repeated_grid_count: int = 0
    entities: tuple[PerceivedEntity, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "observation": dict(self.observation) if isinstance(self.observation, Mapping) else self.observation,
            "grid_hash": self.grid_hash,
            "grid_shape": self.grid_shape,
            "loop_signal": self.loop_signal,
            "repeated_grid_count": self.repeated_grid_count,
            "entities": [e.to_dict() for e in self.entities],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PerceptionSnapshot:
        return cls(
            observation=d["observation"],
            grid_hash=d["grid_hash"],
            grid_shape=d.get("grid_shape"),
            loop_signal=d.get("loop_signal", False),
            repeated_grid_count=d.get("repeated_grid_count", 0),
            entities=tuple(PerceivedEntity.from_dict(e) for e in d.get("entities", [])),
            metadata=d.get("metadata", {}),
        )


@dataclass(slots=True)
class GoalHypothesis:
    goal_id: str
    description: str
    confidence: float = 0.0
    evidence: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "goal_id": self.goal_id,
            "description": self.description,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> GoalHypothesis:
        return cls(
            goal_id=d["goal_id"],
            description=d["description"],
            confidence=d.get("confidence", 0.0),
            evidence=tuple(d.get("evidence", [])),
            metadata=d.get("metadata", {}),
        )


@dataclass(slots=True)
class ResolvedGoal:
    selected: GoalHypothesis
    alternatives: tuple[GoalHypothesis, ...] = ()
    grounding_gate_passed: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "selected": self.selected.to_dict(),
            "alternatives": [a.to_dict() for a in self.alternatives],
            "grounding_gate_passed": self.grounding_gate_passed,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ResolvedGoal:
        return cls(
            selected=GoalHypothesis.from_dict(d["selected"]),
            alternatives=tuple(GoalHypothesis.from_dict(a) for a in d.get("alternatives", [])),
            grounding_gate_passed=d.get("grounding_gate_passed", True),
            metadata=d.get("metadata", {}),
        )


@dataclass(slots=True)
class PlanCandidate:
    action_id: str
    goal_id: str | None = None
    score: float = 0.0
    rationale: str = ""
    expected_effect: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    # Structured falsifiable prediction for evaluator checks.
    # Schema: {"kind": "grid_change"|"no_change"|"level_gain"|"state_change", "confidence": float}
    predicted_outcome: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    book_id: str = ""

    def __post_init__(self) -> None:
        if not self.book_id:
            self.book_id = str(self.metadata.get("book_id") or self.action_id)

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "goal_id": self.goal_id,
            "score": self.score,
            "rationale": self.rationale,
            "expected_effect": self.expected_effect,
            "payload": self.payload,
            "predicted_outcome": self.predicted_outcome,
            "metadata": self.metadata,
            "book_id": self.book_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PlanCandidate:
        return cls(
            action_id=d["action_id"],
            goal_id=d.get("goal_id"),
            score=d.get("score", 0.0),
            rationale=d.get("rationale", ""),
            expected_effect=d.get("expected_effect"),
            payload=d.get("payload", {}),
            predicted_outcome=d.get("predicted_outcome", {}),
            metadata=d.get("metadata", {}),
            book_id=d.get("book_id", ""),
        )


@dataclass(slots=True)
class PlanningResult:
    candidate: PlanCandidate | None
    alternatives: tuple[PlanCandidate, ...] = ()
    needs_vet: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "candidate": self.candidate.to_dict() if self.candidate else None,
            "alternatives": [a.to_dict() for a in self.alternatives],
            "needs_vet": self.needs_vet,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PlanningResult:
        return cls(
            candidate=PlanCandidate.from_dict(d["candidate"]) if d.get("candidate") else None,
            alternatives=tuple(PlanCandidate.from_dict(a) for a in d.get("alternatives", [])),
            needs_vet=d.get("needs_vet", True),
            metadata=d.get("metadata", {}),
        )


@dataclass(slots=True)
class VetDecision:
    approved: bool
    candidate: PlanCandidate | None = None
    reason: str = ""
    alternative: PlanCandidate | None = None
    should_replan: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "approved": self.approved,
            "candidate": self.candidate.to_dict() if self.candidate else None,
            "reason": self.reason,
            "alternative": self.alternative.to_dict() if self.alternative else None,
            "should_replan": self.should_replan,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> VetDecision:
        return cls(
            approved=d["approved"],
            candidate=PlanCandidate.from_dict(d["candidate"]) if d.get("candidate") else None,
            reason=d.get("reason", ""),
            alternative=PlanCandidate.from_dict(d["alternative"]) if d.get("alternative") else None,
            should_replan=d.get("should_replan", False),
            metadata=d.get("metadata", {}),
        )
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecutionResult:
    action_id: str
    candidate: PlanCandidate | None
    observation: Mapping[str, Any]
    did_progress: bool = False
    predicted_effect: str | None = None
    actual_effect: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "candidate": self.candidate.to_dict() if self.candidate else None,
            "observation": dict(self.observation) if isinstance(self.observation, Mapping) else self.observation,
            "did_progress": self.did_progress,
            "predicted_effect": self.predicted_effect,
            "actual_effect": self.actual_effect,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ExecutionResult:
        return cls(
            action_id=d["action_id"],
            candidate=PlanCandidate.from_dict(d["candidate"]) if d.get("candidate") else None,
            observation=d["observation"],
            did_progress=d.get("did_progress", False),
            predicted_effect=d.get("predicted_effect"),
            actual_effect=d.get("actual_effect"),
            metadata=d.get("metadata", {}),
        )


@dataclass(slots=True)
class EvaluationResult:
    decision: WorkflowDecision
    meaningful_progress: bool
    falsification_delta: int = 0
    reason: str = ""
    next_goal: ResolvedGoal | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "decision": self.decision.value,
            "meaningful_progress": self.meaningful_progress,
            "falsification_delta": self.falsification_delta,
            "reason": self.reason,
            "next_goal": self.next_goal.to_dict() if self.next_goal else None,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> EvaluationResult:
        return cls(
            decision=WorkflowDecision(d["decision"]),
            meaningful_progress=d["meaningful_progress"],
            falsification_delta=d.get("falsification_delta", 0),
            reason=d.get("reason", ""),
            next_goal=ResolvedGoal.from_dict(d["next_goal"]) if d.get("next_goal") else None,
            metadata=d.get("metadata", {}),
        )


@dataclass(slots=True)
class ReasonerOutcome:
    """A202: the orchestrator-facing result of one Reasoner cycle (see
    agents/arc4/investigation_reasoner.py for the pure state machine this
    wraps, and agents/arc4/reasoner_signals.py for the glue that produces
    this). Lives in types.py (not ports.py, matching where PlanningResult/
    VetDecision/etc. already live) and carries `decision` as a plain str
    (investigation_reasoner.ReasonerDecision's value) rather than importing
    that enum here, so the dependency direction stays one-way: reasoner
    modules may import types.py, never the reverse."""

    decision: str  # one of "advance" | "repeat_deepen" | "repeat_retry" | "terminate"
    anchor_ref: Any | None = None
    anchor_type: str | None = None  # "goal" | "entity" | None
    required_action_id: str | None = None  # set only for REPEAT_RETRY -- the exact action to re-propose
    required_book_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "anchor_ref": self.anchor_ref,
            "anchor_type": self.anchor_type,
            "required_action_id": self.required_action_id,
            "required_book_id": self.required_book_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ReasonerOutcome:
        return cls(
            decision=d["decision"],
            anchor_ref=d.get("anchor_ref"),
            anchor_type=d.get("anchor_type"),
            required_action_id=d.get("required_action_id"),
            required_book_id=d.get("required_book_id"),
        )


T = TypeVar("T")


@dataclass(slots=True)
class PhaseResult(Generic[T]):
    phase: WorkflowPhase
    status: PhaseStatus = PhaseStatus.OK
    payload: T | None = None
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        payload_dict = None
        if self.payload is not None:
            if hasattr(self.payload, "to_dict"):
                payload_dict = self.payload.to_dict()
            else:
                payload_dict = self.payload
        return {
            "phase": self.phase.value,
            "status": self.status.value,
            "payload": payload_dict,
            "reason": self.reason,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PhaseResult:
        return cls(
            phase=WorkflowPhase(d["phase"]),
            status=PhaseStatus(d.get("status", "ok")),
            payload=d.get("payload"),
            reason=d.get("reason"),
            metadata=d.get("metadata", {}),
        )


@dataclass(slots=True)
class WorkflowState:
    step_index: int = 0
    phase_runs: int = 0
    terminated: bool = False
    termination_state: WorkflowStatus = WorkflowStatus.RUNNING
    previous_grid_hash: str | None = None
    # A170: ephemeral cache of the actual prior grid (not just its hash) so
    # perception can diff before/after cell changes. Deliberately excluded
    # from to_dict()/from_dict() -- runtime-only, not persisted state.
    previous_grid: list[list[Any]] | None = None
    # A175: ephemeral cache of the prior step's entities (for frame-to-frame
    # correspondence matching) and a monotonic counter for minting fresh
    # correspondence ids. Deliberately excluded from to_dict()/from_dict() --
    # runtime-only, not persisted state.
    previous_entities: tuple[PerceivedEntity, ...] | None = None
    next_entity_ref: int = 0
    loop_history: list[str] = field(default_factory=list)
    loop_history_pointer: int = -1
    active_goal: ResolvedGoal | None = None
    consecutive_no_progress_count: int = 0
    action_attempt_counts: dict[str, int] = field(default_factory=dict)
    action_falsification_counts: dict[str, int] = field(default_factory=dict)
    goal_failure_counts: dict[str, int] = field(default_factory=dict)
    latest_veto_reason: str | None = None
    latest_veto_alternative: PlanCandidate | None = None
    replan_passes: int = 0
    crash_traceback: str | None = None
    # A183: running counts of *confirmed* successful graph writes this
    # episode (result status == "ok", not "no_changes"/"capability_missing"/
    # "error") -- node_writes for ingest_perception (GridEntity/GridSnapshot)
    # and record_transition (Transition), edge_writes for record_rule_evidence
    # (PREDICTS/CONFIRMED_BY/FALSIFIED_BY). A client-side approximation of
    # real graph growth, not an exact node/edge count -- replaces the
    # previous world_model_node_count/edge_count telemetry, which counted
    # unrelated in-memory list sizes (perceived entities, goal/plan
    # alternatives) and had no connection to the actual graph at all.
    world_model_node_writes: int = 0
    world_model_edge_writes: int = 0
    # A202: which investigation thread (if any) the trajectory Reasoner is
    # currently anchored on, tracked in-process across cycles so a fresh
    # thread is only started when the previous one actually concluded
    # (SATISFIED/EXHAUSTED -> ADVANCE). Shape when set:
    # {"anchor_ref": ..., "anchor_type": "goal"|"entity", "thread_id": ...,
    # "state": "exploring", "deepening_cycle_count": 0, "already_retried": False}
    # -- "state" is one of investigation_reasoner.InvestigationState's values,
    # kept as a plain str here for the same one-way-dependency reason
    # ReasonerOutcome.decision is a str above.
    active_investigation_anchor: dict[str, Any] | None = None
    # A202: the most recent REPEAT_DEEPEN/REPEAT_RETRY outcome, consumed by a
    # later card (A203) to bias goal_resolver/plan_generator toward the
    # Reasoner's chosen anchor. None whenever the last outcome was
    # advance/terminate (nothing to bias toward).
    reasoner_anchor_hint: ReasonerOutcome | None = None

    def to_dict(self) -> dict:
        return {
            "step_index": self.step_index,
            "phase_runs": self.phase_runs,
            "terminated": self.terminated,
            "termination_state": self.termination_state.value,
            "previous_grid_hash": self.previous_grid_hash,
            "loop_history": self.loop_history,
            "loop_history_pointer": self.loop_history_pointer,
            "active_goal": self.active_goal.to_dict() if self.active_goal else None,
            "consecutive_no_progress_count": self.consecutive_no_progress_count,
            "action_attempt_counts": self.action_attempt_counts,
            "action_falsification_counts": self.action_falsification_counts,
            "goal_failure_counts": self.goal_failure_counts,
            "latest_veto_reason": self.latest_veto_reason,
            "latest_veto_alternative": self.latest_veto_alternative.to_dict() if self.latest_veto_alternative else None,
            "replan_passes": self.replan_passes,
            "crash_traceback": self.crash_traceback,
            "world_model_node_writes": self.world_model_node_writes,
            "world_model_edge_writes": self.world_model_edge_writes,
            "active_investigation_anchor": self.active_investigation_anchor,
            "reasoner_anchor_hint": self.reasoner_anchor_hint.to_dict() if self.reasoner_anchor_hint else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> WorkflowState:
        return cls(
            step_index=d.get("step_index", 0),
            phase_runs=d.get("phase_runs", 0),
            terminated=d.get("terminated", False),
            termination_state=WorkflowStatus(d.get("termination_state", "running")),
            previous_grid_hash=d.get("previous_grid_hash"),
            loop_history=d.get("loop_history", []),
            loop_history_pointer=d.get("loop_history_pointer", -1),
            active_goal=ResolvedGoal.from_dict(d["active_goal"]) if d.get("active_goal") else None,
            consecutive_no_progress_count=d.get("consecutive_no_progress_count", 0),
            action_attempt_counts=d.get("action_attempt_counts", {}),
            action_falsification_counts=d.get("action_falsification_counts", {}),
            goal_failure_counts=d.get("goal_failure_counts", {}),
            latest_veto_reason=d.get("latest_veto_reason"),
            latest_veto_alternative=PlanCandidate.from_dict(d["latest_veto_alternative"]) if d.get("latest_veto_alternative") else None,
            replan_passes=d.get("replan_passes", 0),
            crash_traceback=d.get("crash_traceback"),
            world_model_node_writes=d.get("world_model_node_writes", 0),
            world_model_edge_writes=d.get("world_model_edge_writes", 0),
            active_investigation_anchor=d.get("active_investigation_anchor"),
            reasoner_anchor_hint=ReasonerOutcome.from_dict(d["reasoner_anchor_hint"]) if d.get("reasoner_anchor_hint") else None,
        )


@dataclass(slots=True)
class WorkflowRunResult:
    status: WorkflowStatus
    state: WorkflowState
    phase_results: list[PhaseResult[Any]] = field(default_factory=list)
    reason: str | None = None
    traceback: str | None = None
    completed_cycles: int = 0

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "state": self.state.to_dict(),
            "phase_results": [p.to_dict() for p in self.phase_results],
            "reason": self.reason,
            "traceback": self.traceback,
            "completed_cycles": self.completed_cycles,
        }

    @classmethod
    def from_dict(cls, d: dict) -> WorkflowRunResult:
        return cls(
            status=WorkflowStatus(d["status"]),
            state=WorkflowState.from_dict(d["state"]),
            phase_results=[PhaseResult.from_dict(p) for p in d.get("phase_results", [])],
            reason=d.get("reason"),
            traceback=d.get("traceback"),
            completed_cycles=d.get("completed_cycles", 0),
        )
