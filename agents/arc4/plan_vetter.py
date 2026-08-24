"""Deterministic ARC v2 plan vetter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .ports import GraphQueryPort
from .types import PerceptionSnapshot, PhaseResult, PhaseStatus, PlanCandidate, PlanningResult, ResolvedGoal, VetDecision, WorkflowPhase, WorkflowState


@dataclass(slots=True)
class PlanVetterLimits:
    repeated_falsification_threshold: int = 2
    weak_evidence_score_threshold: float = 0.4
    excessive_repetition_threshold: int = 3  # veto after N attempts with weak evidence


class PlanVetter:
    """Apply a deterministic go/no-go gate to the selected plan candidate."""

    def __init__(self, limits: PlanVetterLimits | None = None, *, graph_port: GraphQueryPort | None = None) -> None:
        self._limits = limits or PlanVetterLimits()
        self._graph_port = graph_port

    def __call__(
        self,
        state: WorkflowState,
        perception: PerceptionSnapshot,
        goal: ResolvedGoal,
        plan: PlanningResult,
    ) -> PhaseResult[VetDecision]:
        return self.vet(state, perception, goal, plan)

    def vet(
        self,
        state: WorkflowState,
        perception: PerceptionSnapshot,
        goal: ResolvedGoal,
        plan: PlanningResult,
    ) -> PhaseResult[VetDecision]:
        del perception, goal

        candidate = plan.candidate
        if candidate is None:
            decision = VetDecision(
                approved=False,
                candidate=None,
                reason="missing plan candidate",
                should_replan=True,
            )
            return PhaseResult(phase=WorkflowPhase.VET, status=PhaseStatus.VETO, payload=decision, reason=decision.reason)

        candidate_book_id = candidate.book_id
        candidate_falsifications = int(state.action_falsification_counts.get(candidate_book_id, 0))
        candidate_attempts = int(state.action_attempt_counts.get(candidate_book_id, 0))
        alternative = self._choose_alternative(state, candidate, plan.alternatives)

        # A135: Graph-backed action gate — ask the graph if this action should be allowed
        graph_gate = self._check_graph_gate(candidate.action_id)
        if not graph_gate.get("allowed", True) and alternative is not None:
            decision = VetDecision(
                approved=False,
                candidate=candidate,
                reason=f"graph evidence blocks {candidate.action_id}: {graph_gate.get('reason', 'negative evidence')}",
                alternative=alternative,
                should_replan=True,
                metadata={
                    "candidate_attempts": candidate_attempts,
                    "candidate_falsifications": candidate_falsifications,
                    "veto_type": "graph_evidence",
                    "graph_gate": graph_gate,
                },
            )
            return PhaseResult(phase=WorkflowPhase.VET, status=PhaseStatus.VETO, payload=decision, reason=decision.reason)

        if candidate_falsifications >= self._limits.repeated_falsification_threshold and alternative is not None:
            decision = VetDecision(
                approved=False,
                candidate=candidate,
                reason=f"{candidate.action_id} has been falsified {candidate_falsifications} times",
                alternative=alternative,
                should_replan=True,
                metadata={
                    "candidate_attempts": candidate_attempts,
                    "candidate_falsifications": candidate_falsifications,
                    "veto_type": "repeated_falsification",
                },
            )
            return PhaseResult(phase=WorkflowPhase.VET, status=PhaseStatus.VETO, payload=decision, reason=decision.reason)

        # A132: Veto excessively repeated action with weak evidence
        if (
            candidate_attempts >= self._limits.excessive_repetition_threshold
            and candidate.score < self._limits.weak_evidence_score_threshold
            and alternative is not None
        ):
            decision = VetDecision(
                approved=False,
                candidate=candidate,
                reason=f"{candidate.action_id} attempted {candidate_attempts} times with weak evidence (score={candidate.score:.2f})",
                alternative=alternative,
                should_replan=True,
                metadata={
                    "candidate_attempts": candidate_attempts,
                    "candidate_falsifications": candidate_falsifications,
                    "veto_type": "excessive_repetition",
                },
            )
            return PhaseResult(phase=WorkflowPhase.VET, status=PhaseStatus.VETO, payload=decision, reason=decision.reason)

        warnings = self._weak_evidence_warning(candidate, candidate_falsifications)
        decision = VetDecision(
            approved=True,
            candidate=candidate,
            reason=warnings[0] if warnings else "approved",
            alternative=alternative,
            should_replan=False,
            metadata={
                "candidate_attempts": candidate_attempts,
                "candidate_falsifications": candidate_falsifications,
                "warnings": warnings,
                "graph_gate": graph_gate,
            },
        )
        return PhaseResult(phase=WorkflowPhase.VET, status=PhaseStatus.OK, payload=decision, reason=decision.reason)

    def _check_graph_gate(self, action_id: str) -> dict[str, Any]:
        """Query the graph for an action go/no-go signal. Returns allowed=True if no graph port."""
        if self._graph_port is None:
            return {"allowed": True, "reason": "no_graph_port"}
        try:
            return self._graph_port.check_action_gate(action_id)
        except Exception:
            return {"allowed": True, "reason": "graph_error"}

    def _choose_alternative(
        self,
        state: WorkflowState,
        candidate: PlanCandidate,
        alternatives: tuple[PlanCandidate, ...],
    ) -> PlanCandidate | None:
        for alternative in alternatives:
            if alternative.action_id == candidate.action_id:
                continue
            if int(state.action_attempt_counts.get(alternative.book_id, 0)) == 0:
                return alternative
        if state.latest_veto_alternative is not None and state.latest_veto_alternative.action_id != candidate.action_id:
            if int(state.action_attempt_counts.get(state.latest_veto_alternative.book_id, 0)) == 0:
                return state.latest_veto_alternative
        return None

    def _weak_evidence_warning(self, candidate: PlanCandidate, falsifications: int) -> list[str]:
        warnings: list[str] = []
        if candidate.score < self._limits.weak_evidence_score_threshold:
            warnings.append(f"weak evidence for {candidate.action_id}")
        if falsifications > 0 and falsifications < self._limits.repeated_falsification_threshold:
            warnings.append(f"{candidate.action_id} has been falsified {falsifications} time(s)")
        return warnings
