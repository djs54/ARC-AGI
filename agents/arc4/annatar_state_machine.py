"""Pure investigation-thread state machine for the trajectory Annatar
(docs/superpowers/specs/2026-08-23-trajectory-reasoner-design.md, section 4).

Deterministic, stdlib only, no graph/LLM/I/O -- mirrors cycle_policy.py's
own discipline. The caller (A202's orchestrator integration) is responsible
for computing CycleSignals from WorkflowState and graph queries; this
module never queries anything itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class InvestigationState(StrEnum):
    EXPLORING = "exploring"
    DEEPENING = "deepening"
    AWAITING_LLM = "awaiting_llm"
    SATISFIED = "satisfied"
    EXHAUSTED = "exhausted"
    RETRY = "retry"


class AnnatarDecision(StrEnum):
    ADVANCE = "advance"
    REPEAT_DEEPEN = "repeat_deepen"
    REPEAT_RETRY = "repeat_retry"
    TERMINATE = "terminate"


@dataclass(slots=True)
class CycleSignals:
    """Pre-computed signals the transition table reads. All fields are
    computed by the caller; this dataclass carries no logic of its own."""

    meaningful_progress: bool
    confidence: float
    untested_remaining: bool
    all_falsified: bool
    execution_inconclusive: bool
    deepening_cycle_count: int
    already_retried: bool
    # A205: set by the I/O layer (annatar_signals.compute_cycle_signals)
    # when a graph-client call raised during this cycle's signal
    # computation, so the failure is visible instead of only being
    # silently absorbed into a conservative default. Still just a plain
    # data field -- this module remains zero-I/O; it never sets this
    # itself, only carries what the caller computed.
    degraded: bool = False
    # A212: visibility-only fields (audit conclusion: a first plan_vetter
    # veto is a bounded, deterministic Shift-A signal -- like check_budget
    # (A209) it should be *informed* to Annatar, not empowered to decide
    # anything. Set by compute_cycle_signals from WorkflowState.latest_veto_
    # reason/alternative exactly when a veto occurred earlier in the SAME
    # cycle whose successful local resolve/plan/vet retry then let the cycle
    # reach execute/evaluate/annatar normally. transition() below never
    # reads either field -- they carry no decision weight, matching this
    # card's requirement that visibility must not alter the local replan's
    # own control flow or Annatar's decision logic.
    veto_reason: str | None = None
    veto_alternative_action_id: str | None = None


@dataclass(slots=True)
class AnnatarLimits:
    """Starting-point thresholds, no empirical basis yet -- see spec section
    11. Tune with real data once this lands, don't treat these as final."""

    satisfied_confidence_threshold: float = 0.75
    max_deepening_cycles_before_llm: int = 3


def transition(
    current_state: InvestigationState,
    signals: CycleSignals,
    limits: AnnatarLimits | None = None,
) -> InvestigationState:
    """Deterministic transition table, spec section 4.2. Does not resolve
    AWAITING_LLM -- that's apply_llm_vote's job, called separately by the
    integration layer once an LLM answer is available."""
    limits = limits or AnnatarLimits()

    if current_state == InvestigationState.RETRY:
        if not signals.execution_inconclusive:
            return transition(InvestigationState.EXPLORING, signals, limits)
        return InvestigationState.EXHAUSTED

    if current_state in (InvestigationState.EXPLORING, InvestigationState.DEEPENING):
        if signals.execution_inconclusive and not signals.already_retried:
            return InvestigationState.RETRY
        if signals.confidence >= limits.satisfied_confidence_threshold or signals.meaningful_progress:
            return InvestigationState.SATISFIED
        if signals.all_falsified and not signals.untested_remaining:
            return InvestigationState.EXHAUSTED
        if (
            current_state == InvestigationState.DEEPENING
            and signals.deepening_cycle_count >= limits.max_deepening_cycles_before_llm
        ):
            return InvestigationState.AWAITING_LLM
        return InvestigationState.DEEPENING

    if current_state == InvestigationState.AWAITING_LLM:
        raise ValueError("AWAITING_LLM must be resolved via apply_llm_vote(), not transition()")

    raise ValueError(f"unknown state: {current_state}")


def permissible_llm_transitions(signals: CycleSignals) -> frozenset[InvestigationState]:
    """The set of states an LLM vote is allowed to land on, given current
    graph signals -- the "graph bounds the permissible paths" enforcement
    point. EXHAUSTED is only legal when the graph itself confirms nothing
    untested/unfalsified remains."""
    permitted = {InvestigationState.DEEPENING, InvestigationState.SATISFIED}
    if not signals.untested_remaining or signals.all_falsified:
        permitted.add(InvestigationState.EXHAUSTED)
    return frozenset(permitted)


def apply_llm_vote(vote: InvestigationState, signals: CycleSignals) -> InvestigationState:
    """Validate an LLM's proposed transition against what the graph
    currently permits. An out-of-set vote is never honored."""
    permitted = permissible_llm_transitions(signals)
    if vote in permitted:
        return vote
    if InvestigationState.EXHAUSTED in permitted:
        return InvestigationState.EXHAUSTED
    return InvestigationState.DEEPENING


def decision_for_state(new_state: InvestigationState) -> AnnatarDecision:
    """Map a resolved investigation-thread state to what the orchestrator
    does next. SATISFIED/EXHAUSTED end THIS thread, not the episode -- both
    map to ADVANCE (start a fresh thread on a new anchor). Whole-episode
    TERMINATE is decided by the integration layer (A202), which alone knows
    whether there's anything left to advance to -- never by this function."""
    if new_state in (InvestigationState.SATISFIED, InvestigationState.EXHAUSTED):
        return AnnatarDecision.ADVANCE
    if new_state == InvestigationState.RETRY:
        return AnnatarDecision.REPEAT_RETRY
    if new_state in (InvestigationState.DEEPENING, InvestigationState.EXPLORING):
        return AnnatarDecision.REPEAT_DEEPEN
    raise ValueError(f"no decision mapping for state: {new_state}")


__all__ = [
    "InvestigationState",
    "AnnatarDecision",
    "CycleSignals",
    "AnnatarLimits",
    "transition",
    "permissible_llm_transitions",
    "apply_llm_vote",
    "decision_for_state",
]
