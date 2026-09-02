"""A202: turns real runtime state (WorkflowState, perception, execution,
evaluation, graph queries) into annatar_state_machine.CycleSignals, and
implements the AnnatarPhase glue that ties A200's pure state machine and
A201's graph client together each cycle.

Deliberately a separate module from annatar_state_machine.py (which must
stay zero-I/O per A200's acceptance criteria -- this module is the only
place allowed to call graph_port/llm_port) and from workflow.py (which
stays thin per its own existing design principle: "routes phases, enforces
gates, does not reason").
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .annatar_state_machine import (
    CycleSignals,
    CynefinDomain,
    InvestigationState,
    AnnatarDecision,
    ReadinessStatus,
    apply_llm_vote,
    classify_domain,
    decision_for_state,
    permissible_llm_transitions,
    transition,
)
from .ports import GraphQueryPort, LLMMessage, LLMPort
from .types import EvaluationResult, ExecutionResult, PerceptionSnapshot, AnnatarOutcome, WorkflowState


@dataclass(slots=True)
class EntityNeighborhoodClassification:
    """A226: the full result of classifying one entity's graph evidence --
    classify_entity_domain (below) exposes just `.domain` for callers that
    only need the Cynefin classification (classify_all_entity_domains, the
    readiness gate); compute_cycle_signals and plan_generator.py's
    _build_candidates need the raw live hypotheses/rules too (for confidence
    scoring and A208's hard-exclusion respectively), and previously each
    re-implemented the whole fetch+classify sequence independently just to
    get at them -- three copies of the same logic. One fetch_entity_
    neighborhood + conditional one fetch_entity_history per entity, same as
    every one of the three pre-consolidation copies -- no new query."""
    domain: CynefinDomain
    live_hypotheses: list[dict[str, Any]] = field(default_factory=list)
    live_rules: list[dict[str, Any]] = field(default_factory=list)
    had_any_record: bool = False
    degraded: bool = False


def classify_entity_domain_detailed(
    entity_ref: Any, graph_port: GraphQueryPort | None
) -> EntityNeighborhoodClassification:
    """A226: the consolidated implementation -- classify_entity_domain,
    compute_cycle_signals, and plan_generator.py's _build_candidates all
    delegate here instead of each independently re-fetching and
    re-classifying (previously three separate copies of this exact
    sequence). See EntityNeighborhoodClassification's own docstring for why
    the richer return shape exists.

    Degrades to DISORDER on a missing graph_port, an entity_ref with no
    real evidence, or any graph-client exception -- same conservative
    default as every other Cynefin read in this codebase.
    """
    domain = CynefinDomain.DISORDER
    live_hypotheses: list[dict[str, Any]] = []
    live_rules: list[dict[str, Any]] = []
    had_any_record = False
    degraded = False

    if graph_port is None:
        return EntityNeighborhoodClassification(domain=domain)

    fetch_neighborhood = getattr(graph_port, "fetch_entity_neighborhood", None)
    if fetch_neighborhood is not None:
        try:
            neighborhood = fetch_neighborhood(entity_ref)
            hypotheses = neighborhood.get("hypotheses", [])
            rules = neighborhood.get("rules", [])
            had_any_record = bool(hypotheses) or bool(rules)
            live_hypotheses = [h for h in hypotheses if not h.get("falsified")]
            live_rules = [r for r in rules if not r.get("falsified")]
            domain = classify_domain(hypotheses + rules)
        except Exception:
            degraded = True
            domain = CynefinDomain.DISORDER

    if domain == CynefinDomain.DISORDER:
        fetch_history = getattr(graph_port, "fetch_entity_history", None)
        if fetch_history is not None:
            try:
                history = fetch_history(entity_ref)
                transitions = history.get("transitions", []) if isinstance(history, Mapping) else []
                changed_count_total = history.get("changed_count_total", 0) if isinstance(history, Mapping) else 0
                if len(transitions) >= 2 and not changed_count_total:
                    domain = CynefinDomain.CHAOTIC
            except Exception:
                degraded = True

    return EntityNeighborhoodClassification(
        domain=domain,
        live_hypotheses=live_hypotheses,
        live_rules=live_rules,
        had_any_record=had_any_record,
        degraded=degraded,
    )


def classify_entity_domain(entity_ref: Any, graph_port: GraphQueryPort | None) -> CynefinDomain:
    """A224 (consolidated in A226): single-entity Cynefin classification.
    Thin wrapper over classify_entity_domain_detailed -- kept for callers
    that only need the domain value (classify_all_entity_domains, the
    readiness gate). Degrades to DISORDER on a missing graph_port, an
    entity_ref with no real evidence, or any graph-client exception -- same
    conservative default as every other Cynefin read in this codebase."""
    return classify_entity_domain_detailed(entity_ref, graph_port).domain


def classify_all_entity_domains(
    perception: Any,
    graph_port: GraphQueryPort | None,
) -> dict[Any, CynefinDomain]:
    """A224: the readiness gate's own data source -- classify_entity_domain()
    for every entity `perception.entities` currently has an entity_ref for
    (A175's stable, cross-frame correspondence id). Entities without a real
    entity_ref (should not happen in practice post-A175, but not assumed)
    are skipped, not defaulted -- the caller's readiness_status() only cares
    about entities it can actually re-probe."""
    domains: dict[Any, CynefinDomain] = {}
    for entity in getattr(perception, "entities", ()) or ():
        entity_ref = (getattr(entity, "attributes", None) or {}).get("entity_ref")
        if entity_ref is None:
            continue
        domains[entity_ref] = classify_entity_domain(entity_ref, graph_port)
    return domains


def _real_unmapped_entities_remain(perception: Any, graph_port: GraphQueryPort | None) -> bool:
    """A241: the live, graph-grounded bound run_annatar_cycle's whole-
    episode-futility override uses to decide whether resuming the
    readiness-probe loop is warranted -- entities_mapped < entities_total,
    re-derived fresh from the CURRENT graph (via classify_all_entity_
    domains, the exact function arc_runtime/bundle.py's readiness_gate
    closure itself uses), not read from the stale state.readiness_gate_
    entities_mapped/entities_total snapshot the probe phase left behind.
    Monotonic and real (an entity, once resolved out of DISORDER, never
    reverts -- per this card's own design constraint), so this needs no
    separate single-use attempt cap: it is naturally False forever once
    every entity is mapped, the same condition a genuine READY would
    produce."""
    live_domains = classify_all_entity_domains(perception, graph_port)
    entities_total = len(live_domains)
    entities_mapped = sum(1 for d in live_domains.values() if d != CynefinDomain.DISORDER)
    return entities_mapped < entities_total


def compute_cycle_signals(
    state: WorkflowState,
    perception: PerceptionSnapshot,
    execution: ExecutionResult,
    evaluation: EvaluationResult,
    *,
    anchor_ref: Any,
    anchor_type: str,
    deepening_cycle_count: int,
    already_retried: bool,
    graph_port: GraphQueryPort | None = None,
    stall_reason: str | None = None,
    veto_reason: str | None = None,
    veto_alternative_action_id: str | None = None,
    readiness_report: Mapping[str, Any] | None = None,
    resolve_report: Mapping[str, Any] | None = None,
) -> CycleSignals:
    meaningful_progress = bool(evaluation.meaningful_progress)

    confidence = 0.0
    untested_remaining = True
    all_falsified = False
    # A205: visible (not silently swallowed) degraded-mode flag -- set True
    # whenever a graph-client call below raises. The existing safe-default
    # behavior on exception (confidence stays 0.0, untested_remaining stays
    # True) is unchanged; this only adds visibility on top of it.
    degraded = False
    # A217: Cynefin domain, read via classify_entity_domain_detailed (A226)
    # off the same graph evidence `confidence` below is derived from -- no
    # new graph query. Stays DISORDER (the conservative "we don't actually
    # know" default) whenever anchor_type != "entity", fetch_neighborhood is
    # unavailable, or the graph call raises.
    domain = CynefinDomain.DISORDER
    if graph_port is not None:
        # A226: consolidated into classify_entity_domain_detailed -- this
        # used to be its own independent copy of the fetch_entity_
        # neighborhood -> classify_domain -> (if DISORDER) fetch_entity_
        # history -> upgrade-to-CHAOTIC sequence (A217/A218 original),
        # duplicated a second time in plan_generator.py. `confidence` is
        # still derived the same way (max confidence across live hypotheses
        # + live rules) -- classify_entity_domain_detailed exposes those
        # lists precisely so this call site can compute it without
        # re-fetching.
        if anchor_type == "entity":
            classification = classify_entity_domain_detailed(anchor_ref, graph_port)
            domain = classification.domain
            confidence = max(
                (h.get("confidence", 0.0) for h in classification.live_hypotheses + classification.live_rules),
                default=0.0,
            )
            if classification.degraded:
                degraded = True
        fetch_untested = getattr(graph_port, "fetch_untested_actions", None)
        if fetch_untested is not None:
            try:
                untested_remaining = bool(fetch_untested())
            except Exception:
                untested_remaining = True
                degraded = True

    # execution_inconclusive: no clear grid change and no explicit progress
    # signal. Read from evaluation.metadata["grid_changed"] -- evaluator.py
    # (agents/arc4/evaluator.py) is the sole owner of *resolving* this flag
    # (grid_changed_flag, evaluator.py:67-71): it prefers the execution-level
    # value when the real production transport supplies one, and only falls
    # back to its own `not grid_unchanged` computation when that's absent.
    # A240: execution.metadata *is* populated with "grid_changed" by the real
    # production transport -- arc_runtime/game_session.py's _compute_progress
    # returns it, and Executor._normalize_result's dict-shaped branch copies
    # every key except "observation" (grid_changed included) through onto
    # ExecutionResult.metadata. This function still reads from
    # evaluation.metadata rather than execution.metadata, not because the
    # latter is empty, but because evaluation.metadata["grid_changed"] is the
    # *resolved* value -- correct regardless of whether execution-level data
    # happens to be present (e.g. in tests or transports that only supply a
    # bare dict), whereas reading execution.metadata directly here would skip
    # evaluator.py's fallback and silently read None whenever it's absent.
    eval_meta = evaluation.metadata if isinstance(evaluation.metadata, dict) else {}
    execution_inconclusive = not bool(eval_meta.get("grid_changed", False)) and not meaningful_progress

    # A202 (spec section 5's self-review correction): check_stall's signal
    # becomes an *input* to Annatar instead of an independent return
    # path out of WorkflowOrchestrator.run(). check_stall firing means
    # "every available action has been attempted repeatedly with no
    # progress" -- workflow.py's own precise, direct measure of action-space
    # exhaustion, which is strictly more authoritative for this anchor's
    # cycle than the graph-derived all_falsified/untested_remaining above.
    # When it fires, override both toward the shape annatar_state_machine
    # .transition() reads as "nothing left" so the transition table is
    # pushed toward EXHAUSTED, not silently ignored.
    if stall_reason is not None:
        all_falsified = True
        untested_remaining = False

    # A230: purely informational -- set from the readiness-gate's own report
    # (already computed by the readiness_gate dependency, unchanged) when
    # workflow.py's probe-path routes a probe cycle through this same
    # Annatar call site. transition() never reads these fields (see
    # CycleSignals' own docstring); the whole-episode "is exploration
    # complete" decision is computed separately, by run_annatar_cycle below,
    # onto AnnatarOutcome.exploration_complete.
    readiness_status = readiness_report.get("status") if readiness_report is not None else None
    readiness_entities_mapped = readiness_report.get("entities_mapped") if readiness_report is not None else None
    readiness_entities_total = readiness_report.get("entities_total") if readiness_report is not None else None

    # A234: purely informational -- set from goal_resolver.py::resolve()'s
    # own already-computed output (workflow.py builds this dict from
    # resolved_goal_payload right before the normal-cycle Annatar call, see
    # workflow.py's own A234 comment at that call site). Same "informed, not
    # empowered" precedent as readiness_report/veto_reason above --
    # transition() never reads these three fields (see Track A's reasoning
    # on CycleSignals.resolve_hypothesis_ambiguity's own docstring).
    resolve_grounding_gate_passed = (
        resolve_report.get("grounding_gate_passed") if resolve_report is not None else None
    )
    resolve_llm_escalated = resolve_report.get("llm_escalated") if resolve_report is not None else None
    resolve_hypothesis_ambiguity = (
        resolve_report.get("top_two_confidence_gap") if resolve_report is not None else None
    )

    return CycleSignals(
        meaningful_progress=meaningful_progress,
        confidence=confidence,
        untested_remaining=untested_remaining,
        all_falsified=all_falsified,
        execution_inconclusive=execution_inconclusive,
        deepening_cycle_count=deepening_cycle_count,
        already_retried=already_retried,
        degraded=degraded,
        veto_reason=veto_reason,
        veto_alternative_action_id=veto_alternative_action_id,
        domain=domain,
        readiness_status=readiness_status,
        readiness_entities_mapped=readiness_entities_mapped,
        readiness_entities_total=readiness_entities_total,
        resolve_grounding_gate_passed=resolve_grounding_gate_passed,
        resolve_llm_escalated=resolve_llm_escalated,
        resolve_hypothesis_ambiguity=resolve_hypothesis_ambiguity,
    )


def _build_transition_vote_prompt(state: WorkflowState, signals: CycleSignals) -> list[LLMMessage]:
    """Schema-constrained JSON request, matching goal_resolver.py::_query_llm
    / plan_generator.py::_query_llm's exact established convention: a system
    message stating the required-JSON-only contract, a user message carrying
    the actual decision inputs plus a `required_fields` list the model must
    fill in. The LLM is told exactly which states are graph-permitted right
    now (via permissible_llm_transitions) so a well-behaved model votes
    in-set on the first try -- though resolve_llm_vote's caller (run_annatar
    _cycle -> apply_llm_vote) still independently re-validates the vote
    against that same permitted set, never trusting the model's own
    self-reported compliance."""
    permitted = sorted(s.value for s in permissible_llm_transitions(signals))
    return [
        LLMMessage(
            role="system",
            content=(
                "Resolve an ambiguous ARC investigation-thread transition by voting for "
                "exactly one of the permitted next states. Respond with ONLY a JSON object "
                'with exactly these keys: "state" (string, must match one of the permitted '
                'states exactly) and "reason" (string, brief explanation).'
            ),
        ),
        LLMMessage(
            role="user",
            content=json.dumps(
                {
                    "current_state": InvestigationState.AWAITING_LLM.value,
                    "permitted_states": permitted,
                    "signals": {
                        "meaningful_progress": signals.meaningful_progress,
                        "confidence": signals.confidence,
                        "untested_remaining": signals.untested_remaining,
                        "all_falsified": signals.all_falsified,
                        "execution_inconclusive": signals.execution_inconclusive,
                        "deepening_cycle_count": signals.deepening_cycle_count,
                        "already_retried": signals.already_retried,
                    },
                    "required_fields": ["state", "reason"],
                },
                sort_keys=True,
            ),
        ),
    ]


def _parse_transition_vote(response: str) -> str | None:
    """Mirrors goal_resolver.py::_parse_llm_response / plan_generator.py::
    _parse_llm_response's exact fallback shape: try strict JSON first, then
    fall back to a permissive regex scan of the raw text for a `state: ...`
    mention (handles a model that ignores the JSON-only instruction but
    still names its vote in prose). Returns None -- never raises -- on any
    parse failure, so the caller's single `if parsed is None` check handles
    it uniformly with an outright exception from the call itself."""
    if not response:
        return None
    try:
        parsed = json.loads(response)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, Mapping) and parsed.get("state"):
        return str(parsed["state"])

    state_match = re.search(r"state\s*[:=]\s*\"?([A-Za-z_]+)\"?", response, re.IGNORECASE)
    return state_match.group(1) if state_match else None


def resolve_llm_vote(llm_port: LLMPort | None, state: WorkflowState, signals: CycleSignals) -> InvestigationState:
    """AWAITING_LLM escalation: the real bounded LLM call for A205.

    "Bounded" here means exactly one attempt wrapped in one try/except --
    not a multi-attempt retry loop. Deviation from the design spec worth
    calling out explicitly: spec section 8 says to "reuse the existing
    retry/timeout conventions from goal_resolver/plan_generator's own
    escalation calls," but neither goal_resolver.py::_query_llm nor
    plan_generator.py::_query_llm actually implement any retry or timeout
    logic today -- both call `llm_port.chat(messages)` exactly once, with no
    surrounding try/except at all (a raised exception there propagates to
    the caller). There is no existing multi-attempt-retry convention in this
    codebase to reuse. Rather than inventing new, untested-elsewhere retry
    machinery, this function matches the *actual* convention (a single
    `chat()` call) and adds only the safety net this card specifically
    requires: any failure at all (no llm_port, a raised exception, or an
    unparseable/invalid response) resolves to InvestigationState.EXPLORING,
    a sentinel annatar_state_machine.permissible_llm_transitions() never
    includes (confirmed directly against that function's implementation --
    it only ever returns a subset of {DEEPENING, SATISFIED, EXHAUSTED}), so
    apply_llm_vote's existing out-of-set-vote fallback (prefer EXHAUSTED
    when the graph permits it, else DEEPENING) does the actual fallback
    work. No second, bespoke fallback rule is introduced here.
    """
    if llm_port is None:
        return InvestigationState.EXPLORING
    try:
        response = llm_port.chat(_build_transition_vote_prompt(state, signals))
        raw_vote = _parse_transition_vote(response)
        if raw_vote is None:
            return InvestigationState.EXPLORING
        return InvestigationState(raw_vote)
    except Exception:
        return InvestigationState.EXPLORING


DEFAULT_MAX_UNPRODUCTIVE_ANCHORS = 3
# Post-A206 fix (2026-08-25): starting-point value, no empirical basis yet --
# same honest-gap treatment as every other new scoring/threshold constant
# introduced this session. Confirmed live: a stuck puzzle cycled through 4+
# totally unproductive anchors before wall-clock budget ended the episode;
# 3 in a row is a reasonable first guess at "this episode is going nowhere,"
# not a tuned value.


def run_annatar_cycle(
    state: WorkflowState,
    perception: PerceptionSnapshot,
    execution: ExecutionResult,
    evaluation: EvaluationResult,
    *,
    graph_port: GraphQueryPort | None = None,
    llm_port: LLMPort | None = None,
    stall_reason: str | None = None,
    veto_reason: str | None = None,
    veto_alternative_action_id: str | None = None,
    max_unproductive_anchors: int = DEFAULT_MAX_UNPRODUCTIVE_ANCHORS,
    readiness_report: Mapping[str, Any] | None = None,
    resolve_report: Mapping[str, Any] | None = None,
) -> AnnatarOutcome:
    """The actual AnnatarPhase: resolves the current investigation thread's
    state via annatar_state_machine's pure functions, persists the result
    through A201's graph client, and returns the orchestrator-facing
    decision. If state.active_investigation_anchor is None (fresh attempt,
    or the previous thread just concluded), picks a starting anchor from the
    current goal/execution before reasoning: prefer the just-executed
    candidate's entity_ref if it has one (a click just happened, that's the
    natural next anchor), else the active goal's goal_id.

    Whole-episode futility (2026-08-25 fix): the per-anchor state machine
    already recognizes when ONE anchor is going nowhere (EXHAUSTED/RETRY),
    but nothing aggregated across DIFFERENT anchors -- a puzzle where every
    anchor tried is equally dead would just cycle through anchors forever
    until check_budget's wall-clock ceiling ended it, never producing a real
    decision. `anchor["any_progress"]` tracks whether THIS anchor has ever
    registered meaningful_progress across its whole life; when an anchor
    concludes (ADVANCE) without ever having shown progress,
    state.annatar_unproductive_anchor_streak increments -- any anchor that
    DOES show progress resets it to 0. Crossing max_unproductive_anchors
    overrides the decision to TERMINATE (an existing workflow.py code path
    that decision_for_state() itself was documented as never actually
    producing).

    `readiness_report` (A230): the Cynefin readiness gate's own report
    (`{"status": ReadinessStatus, "entities_mapped": int, "entities_total":
    int, ...}`, already computed by the readiness_gate dependency exactly as
    before this card) -- passed through by workflow.py's probe-path block on
    every probe cycle so Annatar actually sees the whole-perception
    exploration-coverage question instead of workflow.py deciding it alone.
    Threaded into CycleSignals (informational only, see compute_cycle_
    signals) and used here, independently of the per-anchor transition
    table, to compute AnnatarOutcome.exploration_complete: True for READY/
    PARTIAL_FALLTHROUGH, False for NOT_READY, None when no report was passed
    (normal post-readiness-gate cycles, or no readiness gate configured at
    all).

    `resolve_report` (A234): goal_resolver.py::resolve()'s own already-
    computed per-cycle output (`{"grounding_gate_passed": bool,
    "llm_escalated": bool, "llm_reason": str | None, "top_two_confidence_
    gap": float | None}`), built by workflow.py's normal-cycle call site
    from the `resolved_goal_payload` it already holds. Threaded into
    CycleSignals (informational only, see compute_cycle_signals) exactly
    like readiness_report above -- no new decision logic here, no
    AnnatarOutcome field of its own. See Track A's reasoning (backlog/
    A234.md's Outcome, and CycleSignals.resolve_hypothesis_ambiguity's own
    docstring) for why this stays purely informational rather than gaining
    an exploration_complete-style aggregate the way readiness_report did.

    `AnnatarOutcome.resume_mapping` (A241): set True by the whole-episode-
    futility override below exactly when it would otherwise TERMINATE, but
    the readiness gate hit PARTIAL_FALLTHROUGH at some point this episode
    (state.readiness_gate_partial) AND a fresh, live re-derivation of the
    graph (not the stale post-probe-phase snapshot) shows real unmapped
    territory still remains. Mirrors exploration_complete's own shape --
    extra info alongside the raw per-anchor `decision`, not a new
    AnnatarDecision value -- so `decision` itself stays whatever
    decision_for_state produced (ADVANCE) whenever resume_mapping fires;
    workflow.py is the only place that acts on it, resetting
    state.readiness_gate_resolved and routing back into the existing
    probe-path code instead of honoring the override's usual TERMINATE.
    False (never None) here -- unlike exploration_complete, this is only
    ever computed on the non-probe (readiness_report is None) path, where
    "not warranted" is always a real, decidable answer, not "wasn't asked."
    See backlog/A241.md for the full design (why this needed no new
    single-use attempt cap: entities_mapped < entities_total is itself
    real, monotonic, and graph-grounded)."""
    # A205: local degraded flag, visible (not silently discarded) whenever a
    # graph-client call below raises -- threaded into the returned
    # AnnatarOutcome.degraded at the bottom of this function.
    degraded = False
    anchor = state.active_investigation_anchor
    if anchor is None:
        cand_meta = execution.candidate.metadata if execution.candidate is not None else {}
        entity_ref = cand_meta.get("entity_ref") if isinstance(cand_meta, dict) else None
        if entity_ref is not None:
            anchor_ref, anchor_type = entity_ref, "entity"
        else:
            anchor_ref = state.active_goal.selected.goal_id if state.active_goal is not None else None
            anchor_type = "goal"

        thread_id = None
        if graph_port is not None:
            start_or_resume = getattr(graph_port, "start_or_resume_thread", None)
            if start_or_resume is not None:
                try:
                    result = start_or_resume(anchor_ref, anchor_type)
                    thread_id = result.get("thread_id") if isinstance(result, dict) else None
                except Exception:
                    thread_id = None
                    degraded = True

        anchor = {
            "anchor_ref": anchor_ref,
            "anchor_type": anchor_type,
            "thread_id": thread_id,
            "state": InvestigationState.EXPLORING.value,
            "deepening_cycle_count": 0,
            "already_retried": False,
            "any_progress": False,
            # A235: baseline snapshot of state.world_model_edge_writes
            # (A183's confirmed-real-graph-write counter) at anchor
            # creation, so any_progress below can also credit real graph
            # growth this anchor's own investigation caused, not just
            # signals.meaningful_progress (a narrow whole-puzzle-progress
            # boolean that stayed False across a whole live episode that
            # had, in fact, produced real CHAOTIC/COMPLEX/CONVERGED
            # classifications -- see backlog/A235.md).
            #
            # Known limitation (Track A, investigated not assumed): this
            # snapshot is taken AFTER this same cycle's own evaluate phase
            # already ran (workflow.py calls evaluate, then annatar --
            # confirmed by direct read of both the probe-path and
            # normal-path call sites), so a brand-new anchor's very first
            # cycle can never have its own evaluate's graph write credited
            # via this before/after comparison -- only cycle 2+ on the SAME
            # anchor (DEEPENING/RETRY) can register growth. Accepted as a
            # documented limitation rather than fixed via a pre-evaluate
            # snapshot threaded through workflow.py/ports.py/bundle.py:
            # a single-cycle anchor that immediately ADVANCEs was never
            # going to threaten the 3-strike streak on its own (the streak
            # only accumulates across MULTIPLE unproductive anchor
            # conclusions), and the streak has time to matter precisely in
            # the multi-cycle DEEPENING/RETRY case this fix already covers.
            # See backlog/A235.md's Outcome for the live-data check behind
            # this call.
            "edge_writes_at_start": state.world_model_edge_writes,
        }

    current_state = InvestigationState(anchor["state"])
    signals = compute_cycle_signals(
        state,
        perception,
        execution,
        evaluation,
        anchor_ref=anchor["anchor_ref"],
        anchor_type=anchor["anchor_type"],
        deepening_cycle_count=anchor["deepening_cycle_count"],
        already_retried=anchor["already_retried"],
        graph_port=graph_port,
        stall_reason=stall_reason,
        veto_reason=veto_reason,
        veto_alternative_action_id=veto_alternative_action_id,
        readiness_report=readiness_report,
        resolve_report=resolve_report,
    )
    degraded = degraded or signals.degraded
    # A235: graph_grew is a second, independent progress signal alongside
    # meaningful_progress -- real graph growth (a confirmed CONFIRMED_BY/
    # FALSIFIED_BY/PREDICTS edge write, per A183's "only a confirmed real
    # write counts" discipline) attributable to THIS anchor's own
    # investigation counts as progress even when the narrower whole-puzzle
    # meaningful_progress signal never fires. Edge-writes-only (not also
    # node_writes): node writes include routine perception bookkeeping
    # (GridEntity/GridSnapshot writes every cycle regardless of whether
    # anything was actually learned), while edge writes only ever happen on
    # a confirmed rule/transition-evidence write in evaluator.py -- the
    # closer fit for "real causal learning," per this card's investigation.
    # anchor.get(..., state.world_model_edge_writes) guards a pre-existing
    # anchor dict (e.g. built directly in a test fixture, or from state
    # persisted before this card) that predates this field -- defaulting to
    # the current count makes graph_grew False rather than raising or
    # spuriously crediting progress.
    graph_grew = state.world_model_edge_writes > anchor.get("edge_writes_at_start", state.world_model_edge_writes)
    anchor["any_progress"] = anchor.get("any_progress", False) or bool(signals.meaningful_progress) or graph_grew

    # A235 live-verification hook: mirrors workflow.py's PROBE_ANNATAR/
    # RESOLVE_ANNATAR/STALL_CHECK precedent -- a greppable, concrete record
    # of the two independent progress signals feeding any_progress every
    # single cycle (not just on ADVANCE), so a live run can be directly
    # inspected for whether graph_grew credited an anchor that meaningful_
    # progress alone would have missed.
    import logging as _annatar_logging

    _annatar_logging.getLogger(__name__).info(
        "ANCHOR_PROGRESS anchor_ref=%s anchor_type=%s meaningful_progress=%s graph_grew=%s any_progress=%s "
        "edge_writes=%s edge_writes_at_start=%s streak=%s",
        anchor["anchor_ref"],
        anchor["anchor_type"],
        bool(signals.meaningful_progress),
        graph_grew,
        anchor["any_progress"],
        state.world_model_edge_writes,
        anchor.get("edge_writes_at_start"),
        state.annatar_unproductive_anchor_streak,
    )

    if current_state == InvestigationState.AWAITING_LLM:
        vote = resolve_llm_vote(llm_port, state, signals)
        new_state = apply_llm_vote(vote, signals)
    else:
        new_state = transition(current_state, signals)

    write_thread_state = getattr(graph_port, "write_thread_state", None) if graph_port is not None else None
    if write_thread_state is not None and anchor["thread_id"] is not None:
        try:
            write_thread_state(anchor["thread_id"], new_state.value)
        except Exception:
            degraded = True  # decision-durability write failed -- the decision itself still stands

    if new_state == InvestigationState.AWAITING_LLM:
        # Live-smoke-discovered regression (2026-08-25): transition() can
        # itself produce AWAITING_LLM as new_state (a DEEPENING thread whose
        # deepening_cycle_count just reached the limit) -- but
        # decision_for_state() explicitly does not accept AWAITING_LLM as
        # input (must be resolved via apply_llm_vote() first, per
        # annatar_state_machine.py's own docstring) and raises ValueError
        # if handed it directly. apply_llm_vote() itself never *returns*
        # AWAITING_LLM (permissible_llm_transitions() never includes it), so
        # this branch is only ever reached via a fresh transition()
        # escalation, never via the current_state==AWAITING_LLM branch
        # above. Treat it the same as DEEPENING for decision purposes:
        # repeat, park the state, and let the *next* cycle's
        # current_state==AWAITING_LLM branch actually resolve it.
        decision = AnnatarDecision.REPEAT_DEEPEN
    else:
        decision = decision_for_state(new_state)
    resume_mapping = False  # A241: set True only by the whole-episode-futility override below
    if decision.value == "advance":
        state.active_investigation_anchor = None  # thread ended, next cycle picks a fresh anchor
        # A230 (discovered live during this card's own implementation, via
        # the existing tests/test_a141_mock_contract.py mock-harness
        # integration test): annatar_unproductive_anchor_streak (A206) was
        # built and tuned for the GOAL-DIRECTED post-readiness-gate
        # investigation loop, where "advanced without meaningful_progress"
        # really does mean "this click was a dead end." A probe-phase
        # anchor (readiness_report is not None) advancing without
        # meaningful_progress is the OPPOSITE of futile -- it means broad
        # initial entity-mapping is working exactly as designed (A224:
        # "no RETRY/deepening-bias machinery... would prematurely abandon
        # anchors during broad initial mapping" -- see the probe-path
        # comment in workflow.py). Confirmed by direct reproduction: without
        # this guard, any puzzle with >= max_unproductive_anchors untested
        # entities terminates the WHOLE episode partway through probing,
        # before readiness_status() ever reaches READY/PARTIAL_FALLTHROUGH
        # and before goal-directed play is ever attempted -- a severe
        # regression this card must not ship. So: whole-episode-futility
        # tracking only applies to non-probe (readiness_report is None)
        # cycles, exactly as it always has; a probe cycle's ADVANCE neither
        # increments nor resets the streak.
        if readiness_report is None:
            if anchor.get("any_progress"):
                state.annatar_unproductive_anchor_streak = 0
            else:
                state.annatar_unproductive_anchor_streak += 1
            if state.annatar_unproductive_anchor_streak >= max_unproductive_anchors:
                # A241: before honoring whole-episode futility, check whether
                # there is real unmapped territory this episode's readiness
                # gate never got to finish mapping, and resume probing
                # instead of terminating if so. state.readiness_gate_partial
                # (cheap, no graph query -- was the budget-fallthrough safety
                # valve ever hit this episode) gates the more expensive live
                # re-derivation below: an episode that never partially fell
                # through (no readiness gate configured, or the gate reached
                # READY cleanly) has nothing to resume to, so the graph query
                # only runs when it could actually change the answer.
                #
                # The live re-derivation itself (not state.readiness_gate_
                # entities_mapped/entities_total, which is a stale snapshot
                # frozen the moment the probe phase originally concluded) is
                # this card's own Step 1 staleness finding made concrete:
                # plan_generator.py::_build_candidates calls classify_entity_
                # domain_detailed on every ACTION6 candidate's entity_ref --
                # including entities that were never probed (only entities
                # with had_any_record AND nothing_live_remains get hard-
                # excluded; a fresh DISORDER entity is still offered as a
                # click candidate) -- and evaluator.py writes real graph
                # evidence on any executed+evaluated action. Goal-directed
                # play can therefore incidentally resolve some of the
                # "unmapped" entities as a side effect before whole-episode-
                # futility ever fires. Re-deriving fresh here (classify_all_
                # entity_domains, the exact function arc_runtime/bundle.py's
                # readiness_gate closure itself uses) means the resume
                # decision reflects the CURRENT graph, not an over-counted
                # gap that already partially closed on its own -- and it
                # also gives this the same real, monotonic, graph-grounded
                # bound the card calls for (entities_mapped < entities_total)
                # instead of an arbitrary single-use attempt flag.
                resume_mapping = state.readiness_gate_partial and _real_unmapped_entities_remain(
                    perception, graph_port
                )
                if resume_mapping:
                    # Annatar's own decision to resume mapping instead of
                    # terminating -- `decision` is deliberately left as
                    # ADVANCE (decision_for_state's own un-overridden
                    # answer), not a new AnnatarDecision value: every
                    # existing outcome.decision switch site (workflow.py's
                    # three call sites) is completely unaffected by this
                    # signal; AnnatarOutcome.resume_mapping (set at the
                    # bottom of this function) is the one and only thing
                    # workflow.py needs to check to actually resume probing.
                    # Mirrors exploration_complete's own precedent: "extra
                    # info alongside decision," not a decision value itself.
                    #
                    # The streak resets to 0 here so the goal-directed round
                    # that follows the resumed probe phase gets a genuinely
                    # fresh count of max_unproductive_anchors before whole-
                    # episode-futility can fire again -- without this, the
                    # very next unproductive anchor after resuming would
                    # immediately re-cross the (already-at-threshold) streak
                    # and re-fire this same override before the resumed
                    # mapping had any chance to change anything.
                    streak_before_reset = state.annatar_unproductive_anchor_streak
                    state.annatar_unproductive_anchor_streak = 0
                    # Marks exactly when this resume began so arc_runtime/
                    # bundle.py's readiness_gate closure can rebase
                    # readiness_status()'s elapsed-budget-fraction check
                    # against what remained AT THIS MOMENT, instead of the
                    # stale total-episode fraction that already crossed 0.5
                    # the first time PARTIAL_FALLTHROUGH fired (see
                    # WorkflowState.readiness_gate_remap_started_step_index's
                    # own docstring for why a naive reset-and-rerun would
                    # otherwise instantly re-fall-through with zero net
                    # probing).
                    state.readiness_gate_remap_started_step_index = state.step_index
                    # A241 live-verification hook: mirrors the PROBE_ANNATAR/
                    # RESOLVE_ANNATAR/ANCHOR_PROGRESS precedent (A230/A234/
                    # A235) -- a permanent, greppable, concrete record that
                    # whole-episode-futility was about to fire and got
                    # intercepted into a resume instead, so a future live
                    # smoke can directly confirm this path actually fires
                    # rather than inferring it from side effects.
                    import logging as _remap_logging

                    _remap_logging.getLogger(__name__).info(
                        "READINESS_REMAP resume_mapping=True step_index=%s streak_before_reset=%s "
                        "threshold=%s stale_entities_mapped=%s stale_entities_total=%s",
                        state.step_index,
                        streak_before_reset,
                        max_unproductive_anchors,
                        state.readiness_gate_entities_mapped,
                        state.readiness_gate_entities_total,
                    )
                else:
                    # Whole-episode futility: every anchor tried in a row has
                    # been completely dead, and either the readiness gate
                    # never partially fell through (nothing to resume to) or
                    # a fresh re-check shows every entity is already mapped
                    # (nothing left to resume FOR). Override the per-anchor
                    # ADVANCE with a real episode-level decision instead of
                    # silently starting yet another anchor that's likely to
                    # fare the same.
                    decision = AnnatarDecision.TERMINATE
    else:
        anchor["state"] = new_state.value
        if new_state == InvestigationState.DEEPENING:
            anchor["deepening_cycle_count"] += 1
        anchor["already_retried"] = new_state == InvestigationState.RETRY
        state.active_investigation_anchor = anchor

    reports_anchor = decision.value in ("repeat_deepen", "repeat_retry")

    # A230: Annatar's own answer to "is the world model sufficiently
    # explored," computed once here from the readiness-gate's report --
    # never recomputed or re-decided by workflow.py. None (not False) when
    # no report was passed this cycle, distinguishing "Annatar wasn't asked"
    # from "Annatar said not yet."
    exploration_complete: bool | None = None
    if readiness_report is not None:
        readiness_status_value = readiness_report.get("status")
        exploration_complete = readiness_status_value in (
            ReadinessStatus.READY,
            ReadinessStatus.PARTIAL_FALLTHROUGH,
        )

    return AnnatarOutcome(
        decision=decision.value,
        anchor_ref=anchor["anchor_ref"] if reports_anchor else None,
        anchor_type=anchor["anchor_type"] if reports_anchor else None,
        required_action_id=execution.action_id if decision.value == "repeat_retry" else None,
        required_book_id=getattr(execution.candidate, "book_id", None) if decision.value == "repeat_retry" else None,
        degraded=degraded,
        exploration_complete=exploration_complete,
        resume_mapping=resume_mapping,
    )


__all__ = [
    "compute_cycle_signals",
    "resolve_llm_vote",
    "run_annatar_cycle",
    "classify_entity_domain",
    "classify_all_entity_domains",
]
