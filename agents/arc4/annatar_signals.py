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
from typing import Any, Mapping

from .annatar_state_machine import (
    CycleSignals,
    InvestigationState,
    AnnatarDecision,
    apply_llm_vote,
    decision_for_state,
    permissible_llm_transitions,
    transition,
)
from .ports import GraphQueryPort, LLMMessage, LLMPort
from .types import EvaluationResult, ExecutionResult, PerceptionSnapshot, AnnatarOutcome, WorkflowState


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
    if graph_port is not None:
        fetch_neighborhood = getattr(graph_port, "fetch_entity_neighborhood", None)
        if anchor_type == "entity" and fetch_neighborhood is not None:
            try:
                neighborhood = fetch_neighborhood(anchor_ref)
                live_items = [
                    h
                    for h in neighborhood.get("hypotheses", []) + neighborhood.get("rules", [])
                    if not h.get("falsified")
                ]
                confidence = max((h.get("confidence", 0.0) for h in live_items), default=0.0)
            except Exception:
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
    # (agents/arc4/evaluator.py) is the sole owner of computing this flag
    # (it falls back to `not grid_unchanged` when nothing upstream reports
    # one explicitly) and always sets it on EvaluationResult.metadata.
    # execution.metadata is never populated with "grid_changed" by any
    # executor (confirmed by grepping the whole package) -- reading from
    # execution.metadata instead, as an earlier sketch of this function
    # assumed, would silently always read None and produce wrong signals.
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

    return CycleSignals(
        meaningful_progress=meaningful_progress,
        confidence=confidence,
        untested_remaining=untested_remaining,
        all_falsified=all_falsified,
        execution_inconclusive=execution_inconclusive,
        deepening_cycle_count=deepening_cycle_count,
        already_retried=already_retried,
        degraded=degraded,
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
    max_unproductive_anchors: int = DEFAULT_MAX_UNPRODUCTIVE_ANCHORS,
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
    producing)."""
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
    )
    degraded = degraded or signals.degraded
    anchor["any_progress"] = anchor.get("any_progress", False) or bool(signals.meaningful_progress)

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
    if decision.value == "advance":
        state.active_investigation_anchor = None  # thread ended, next cycle picks a fresh anchor
        if anchor.get("any_progress"):
            state.annatar_unproductive_anchor_streak = 0
        else:
            state.annatar_unproductive_anchor_streak += 1
        if state.annatar_unproductive_anchor_streak >= max_unproductive_anchors:
            # Whole-episode futility: every anchor tried in a row has been
            # completely dead. Override the per-anchor ADVANCE with a real
            # episode-level decision instead of silently starting yet
            # another anchor that's likely to fare the same.
            decision = AnnatarDecision.TERMINATE
    else:
        anchor["state"] = new_state.value
        if new_state == InvestigationState.DEEPENING:
            anchor["deepening_cycle_count"] += 1
        anchor["already_retried"] = new_state == InvestigationState.RETRY
        state.active_investigation_anchor = anchor

    reports_anchor = decision.value in ("repeat_deepen", "repeat_retry")
    return AnnatarOutcome(
        decision=decision.value,
        anchor_ref=anchor["anchor_ref"] if reports_anchor else None,
        anchor_type=anchor["anchor_type"] if reports_anchor else None,
        required_action_id=execution.action_id if decision.value == "repeat_retry" else None,
        required_book_id=getattr(execution.candidate, "book_id", None) if decision.value == "repeat_retry" else None,
        degraded=degraded,
    )


__all__ = ["compute_cycle_signals", "resolve_llm_vote", "run_annatar_cycle"]
