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
from typing import Any, Mapping, Sequence


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


class CynefinDomain(StrEnum):
    """A217: Cynefin sense-making domain read off already-fetched
    rule/hypothesis evidence for one investigation anchor (A216 Part 3's
    constraint-based mapping: governing constraint = CONVERGED, enabling
    constraint = COMPLEX, no constraint = CHAOTIC, no evidence at all =
    DISORDER)."""

    DISORDER = "disorder"    # no evidence yet for this anchor
    CONVERGED = "converged"  # live evidence agrees (Clear/Complicated collapsed to one bucket for v1 --
                              # Snowden's distinction between them is about whether expertise was needed
                              # to see an obvious-in-hindsight cause, which doesn't change patience here)
    COMPLEX = "complex"      # live evidence disagrees -- enabling constraint, more probes sense real shape
    CHAOTIC = "chaotic"      # evidence exists but everything's falsified -- no constraint survived


def classify_domain(evidence: Sequence[Mapping[str, Any]]) -> CynefinDomain:
    """Cynefin domain read from already-fetched rule/hypothesis evidence for
    one anchor. Pure, zero-I/O -- mirrors plan_generator.py::_voi_bonus's
    existing agree/disagree check (lines 408-429) but named and reusable,
    not a one-off inline computation.

    Evidence shape note (confirmed against graph_queries.py::
    fetch_entity_neighborhood's actual pass-through and its real fixtures in
    tests/test_a192_entity_neighborhood_candidate_seeding.py): Rule items
    (rule_extraction.py) carry `to_color` as their causal-outcome field;
    Hypothesis items never carry `to_color` at all -- only `hypothesis_id`/
    `claim`/`confidence`/`falsified`. Comparing every item purely on
    `to_color` would collapse all `to_color`-less hypotheses onto a shared
    None bucket, silently reporting CONVERGED even when their `claim`s
    genuinely disagree. So the outcome-comparison key prefers `to_color`
    when present (rules) and falls back to `claim` (hypotheses) -- an item
    with neither field falls back to None uniformly, an acceptable
    degenerate case when no comparison field exists at all."""
    if not evidence:
        return CynefinDomain.DISORDER
    live = [e for e in evidence if not e.get("falsified")]
    if not live:
        return CynefinDomain.CHAOTIC
    distinct_outcomes = {e.get("to_color", e.get("claim")) for e in live}
    return CynefinDomain.CONVERGED if len(distinct_outcomes) <= 1 else CynefinDomain.COMPLEX


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
    # A217: Cynefin domain read off the same fetch_entity_neighborhood
    # evidence compute_cycle_signals already fetches for `confidence` above
    # -- no new graph query. Defaults to DISORDER (the conservative "we
    # don't actually know" case), matching what the I/O layer sets whenever
    # anchor_type != "entity" or the graph call fails/degrades. transition()
    # reads this to scale DEEPENING patience; it carries no other weight.
    domain: CynefinDomain = CynefinDomain.DISORDER
    # A230: informational-only readiness-gate report, threaded through from
    # workflow.py's probe-path call to self._dependencies.annatar(...) via
    # compute_cycle_signals' new `readiness_report` parameter. Same "carries
    # no decision weight for the per-anchor transition" precedent
    # veto_reason/veto_alternative_action_id already establish above --
    # transition() never reads any of these three fields. The actual
    # whole-episode "is exploration complete" decision is computed
    # separately by run_annatar_cycle's own glue code onto
    # AnnatarOutcome.exploration_complete, not by this per-anchor state
    # machine (this module stays zero-I/O and purely per-anchor -- see
    # module docstring).
    readiness_status: "ReadinessStatus | None" = None
    readiness_entities_mapped: int | None = None
    readiness_entities_total: int | None = None


@dataclass(slots=True)
class AnnatarLimits:
    """Starting-point thresholds, no empirical basis yet -- see spec section
    11. Tune with real data once this lands, don't treat these as final."""

    satisfied_confidence_threshold: float = 0.75
    max_deepening_cycles_before_llm: int = 3
    # A217: COMPLEX-domain anchors (live rule/hypothesis evidence genuinely
    # disagrees) get this much more deepening patience before escalating to
    # AWAITING_LLM -- CONVERGED/CHAOTIC/DISORDER anchors are unaffected and
    # keep today's flat max_deepening_cycles_before_llm behavior exactly.
    # Starting-point value, no empirical basis yet -- same honest-gap
    # treatment as every other new threshold in this class.
    complex_domain_deepening_multiplier: float = 2.0


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
        # A221 Finding 5: a COMPLEX anchor (genuine, live, disagreeing
        # evidence) shouldn't be killed by two raw inconclusive clicks --
        # execution_inconclusive is about this single click's own outcome,
        # a different, narrower layer than the accumulated graph evidence
        # domain reads. Recurse into the richer EXPLORING/DEEPENING patience
        # logic instead of a hard exit; already_retried=True (set on
        # entering RETRY) blocks that recursive call from re-triggering
        # RETRY itself, so this can't loop.
        if signals.domain == CynefinDomain.COMPLEX:
            return transition(InvestigationState.EXPLORING, signals, limits)
        return InvestigationState.EXHAUSTED

    if current_state in (InvestigationState.EXPLORING, InvestigationState.DEEPENING):
        if signals.execution_inconclusive and not signals.already_retried:
            # A221 Finding 5: a known-CHAOTIC anchor (e.g. resumed via B369's
            # thread-resume with prior confirmed-dead history) shouldn't
            # waste a RETRY cycle just because this cycle's own click also
            # happened to be inconclusive -- the graph already answered the
            # question RETRY exists to explore.
            #
            # Must not steal a legitimate SATISFIED outcome first. Confidence
            # is provably safe to ignore here: it's always 0.0 whenever
            # domain==CHAOTIC (both derive from the same live-evidence
            # filter in compute_cycle_signals), so the confidence half of
            # SATISFIED's condition could never fire in this branch anyway.
            # meaningful_progress is NOT provably safe the same way -- it's
            # an independent signal from evaluator.py, not derived from graph
            # evidence at all. In real production code
            # execution_inconclusive=True guarantees meaningful_progress=
            # False (compute_cycle_signals's own construction), but this
            # function shouldn't rely on a caller-side invariant it can't see
            # -- check it explicitly instead of assuming it.
            if signals.meaningful_progress:
                return InvestigationState.SATISFIED
            if signals.domain == CynefinDomain.CHAOTIC:
                return InvestigationState.EXHAUSTED
            return InvestigationState.RETRY
        if signals.confidence >= limits.satisfied_confidence_threshold or signals.meaningful_progress:
            return InvestigationState.SATISFIED
        # A221 Finding 1: signals.all_falsified (check_stall, cycle_policy.py)
        # is NOT graph-derived -- it's 100% local WorkflowState counters, a
        # different code path than A194's actual graph-aware fix
        # (evaluator.py::_action_space_exhausted). It used to exit here, but
        # could override a live COMPLEX anchor's own evidence on nothing but
        # unrelated global attempt-fatigue -- backwards from Shift C. Removed
        # from this decision; check_stall's signal remains the sole
        # termination path for the legacy no-Annatar fallback in workflow.py,
        # a separate consumer this change does not touch.
        #
        # CynefinDomain.CHAOTIC (classify_domain(): all rule/hypothesis
        # evidence for this anchor falsified, or A218's confirmed-inert-via-
        # transition-history extension) replaces it -- unlike all_falsified,
        # this genuinely is graph-grounded. Not gated on untested_remaining:
        # that flag is episode-wide (fetch_untested_actions, other action
        # families), unrelated to whether THIS anchor's own evidence is dead;
        # EXHAUSTED here is anchor-scoped (decision_for_state -> ADVANCE,
        # pick a fresh anchor next cycle), not an episode-wide claim.
        if signals.domain == CynefinDomain.CHAOTIC:
            return InvestigationState.EXHAUSTED
        # A217: COMPLEX-domain anchors (live evidence genuinely disagrees)
        # get scaled-up patience before escalating out of DEEPENING.
        # CONVERGED/CHAOTIC/DISORDER all use the flat default, unchanged.
        effective_deepening_limit = limits.max_deepening_cycles_before_llm
        if signals.domain == CynefinDomain.COMPLEX:
            effective_deepening_limit = int(
                limits.max_deepening_cycles_before_llm * limits.complex_domain_deepening_multiplier
            )
        if (
            current_state == InvestigationState.DEEPENING
            and signals.deepening_cycle_count >= effective_deepening_limit
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


class ReadinessStatus(StrEnum):
    """A224: is the graph ready for `resolve` to commit to a goal, or should
    this cycle keep mapping entities instead? See readiness_status()."""

    READY = "ready"                          # every entity has a real classification
    NOT_READY = "not_ready"                  # at least one entity is still DISORDER, budget allows continuing
    PARTIAL_FALLTHROUGH = "partial_fallthrough"  # at least one still DISORDER, budget exhausted -- proceed anyway


def readiness_status(
    entity_domains: Mapping[Any, "CynefinDomain"],
    *,
    step_index: int,
    max_cycles: int,
    budget_fraction_before_fallthrough: float = 0.5,
    untested_non_click_actions: Sequence[str] = (),
) -> ReadinessStatus:
    """A224: the Cynefin readiness gate. Pure, no I/O -- entity_domains is
    {entity_ref: CynefinDomain}, already computed by the caller (classify_domain()
    per entity, via already-fetched fetch_entity_neighborhood/fetch_entity_history
    data) -- this function does not fetch anything itself, mirrors transition()'s
    own "caller computes signals, this function only decides" discipline.

    Deliberately the blunt v0, not Cynefin's actual prescription: not-ready
    means ANY currently-visible entity is still DISORDER (no evidence at
    all), not a smarter per-anchor "evaluate as needed" judgment -- the
    operator's own explicit correction during design: "I don't want this
    phase stopping at 'I've mapped 1 action pretty good so let's move on'".
    The budget_fraction_before_fallthrough safety valve exists because full
    mapping could otherwise consume an entire episode's step budget on a
    busy grid -- PARTIAL_FALLTHROUGH is a real, queryable telemetry fact
    (see Task 5), not a silent failure, generating the trace data needed to
    eventually decide whether a smarter, continuous per-anchor version
    (Cynefin's actual prescription) is viable once RETRY/deepening-bias/the
    streak threshold are also revisited -- not attempted in this card.

    Starting-point budget_fraction_before_fallthrough=0.5, no empirical
    basis yet -- same honest-gap treatment as every other new threshold
    this session.

    A231: `untested_non_click_actions` extends "not fully mapped" to cover
    whole-action-space coverage (fetch_untested_actions, A135), not just
    entity click-coverage -- a puzzle whose real mechanic is a non-click
    action (ACTION1-5) must not report READY while that action has never
    been tried even once. Defaults to `()` so every existing caller that
    doesn't pass it gets byte-for-byte unchanged behavior (regression-tested
    against the full pre-A231 test_a224_readiness_gate.py suite, zero
    assertion edits). The caller (arc_runtime/bundle.py::_readiness_gate) is
    responsible for excluding "ACTION6" from this sequence before calling --
    click coverage is already tracked via entity_domains above; double-
    counting it here would just be the same coverage question asked twice.
    """
    has_disorder_entity = any(domain == CynefinDomain.DISORDER for domain in entity_domains.values())
    has_untested_action = bool(untested_non_click_actions)

    if not has_disorder_entity and not has_untested_action:
        # Nothing left to map/probe means nothing blocks proceeding -- a
        # blank/empty grid with no untested actions either shouldn't stall
        # the episode forever.
        return ReadinessStatus.READY

    if max_cycles <= 0 or (step_index / max_cycles) >= budget_fraction_before_fallthrough:
        return ReadinessStatus.PARTIAL_FALLTHROUGH

    return ReadinessStatus.NOT_READY


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
    "CynefinDomain",
    "classify_domain",
    "CycleSignals",
    "AnnatarLimits",
    "transition",
    "permissible_llm_transitions",
    "apply_llm_vote",
    "decision_for_state",
    "ReadinessStatus",
    "readiness_status",
]
