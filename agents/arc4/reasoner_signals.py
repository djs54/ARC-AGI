"""A202: turns real runtime state (WorkflowState, perception, execution,
evaluation, graph queries) into investigation_reasoner.CycleSignals, and
implements the ReasonerPhase glue that ties A200's pure state machine and
A201's graph client together each cycle.

Deliberately a separate module from investigation_reasoner.py (which must
stay zero-I/O per A200's acceptance criteria -- this module is the only
place allowed to call graph_port/llm_port) and from workflow.py (which
stays thin per its own existing design principle: "routes phases, enforces
gates, does not reason").
"""

from __future__ import annotations

from typing import Any

from .investigation_reasoner import (
    CycleSignals,
    InvestigationState,
    apply_llm_vote,
    decision_for_state,
    transition,
)
from .ports import GraphQueryPort, LLMPort
from .types import EvaluationResult, ExecutionResult, PerceptionSnapshot, ReasonerOutcome, WorkflowState


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
                pass
        fetch_untested = getattr(graph_port, "fetch_untested_actions", None)
        if fetch_untested is not None:
            try:
                untested_remaining = bool(fetch_untested())
            except Exception:
                untested_remaining = True

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
    # becomes an *input* to the Reasoner instead of an independent return
    # path out of WorkflowOrchestrator.run(). check_stall firing means
    # "every available action has been attempted repeatedly with no
    # progress" -- workflow.py's own precise, direct measure of action-space
    # exhaustion, which is strictly more authoritative for this anchor's
    # cycle than the graph-derived all_falsified/untested_remaining above.
    # When it fires, override both toward the shape investigation_reasoner
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
    )


def resolve_llm_vote(llm_port: LLMPort | None, state: WorkflowState, signals: CycleSignals) -> InvestigationState:
    """AWAITING_LLM escalation: the actual bounded-retry LLM call, failure
    handling, and response parsing is A205's responsibility (Reasoner Error
    Handling: Degraded-Mode Fallback + LLM-Escalation Failure Handling).
    This card (A202) only defines the call point -- raise loudly rather than
    silently guessing a vote, so an AWAITING_LLM cycle without a real
    implementation is a visible gap, not a quietly-wrong decision."""
    raise NotImplementedError(
        "resolve_llm_vote has no real implementation yet -- the bounded LLM "
        "retry/timeout/parsing logic for AWAITING_LLM escalation is A205's "
        "responsibility, not A202's."
    )


def run_reasoner_cycle(
    state: WorkflowState,
    perception: PerceptionSnapshot,
    execution: ExecutionResult,
    evaluation: EvaluationResult,
    *,
    graph_port: GraphQueryPort | None = None,
    llm_port: LLMPort | None = None,
    stall_reason: str | None = None,
) -> ReasonerOutcome:
    """The actual ReasonerPhase: resolves the current investigation thread's
    state via investigation_reasoner's pure functions, persists the result
    through A201's graph client, and returns the orchestrator-facing
    decision. If state.active_investigation_anchor is None (fresh attempt,
    or the previous thread just concluded), picks a starting anchor from the
    current goal/execution before reasoning: prefer the just-executed
    candidate's entity_ref if it has one (a click just happened, that's the
    natural next anchor), else the active goal's goal_id."""
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

        anchor = {
            "anchor_ref": anchor_ref,
            "anchor_type": anchor_type,
            "thread_id": thread_id,
            "state": InvestigationState.EXPLORING.value,
            "deepening_cycle_count": 0,
            "already_retried": False,
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
            pass

    decision = decision_for_state(new_state)
    if decision.value == "advance":
        state.active_investigation_anchor = None  # thread ended, next cycle picks a fresh anchor
    else:
        anchor["state"] = new_state.value
        if new_state == InvestigationState.DEEPENING:
            anchor["deepening_cycle_count"] += 1
        anchor["already_retried"] = new_state == InvestigationState.RETRY
        state.active_investigation_anchor = anchor

    return ReasonerOutcome(
        decision=decision.value,
        anchor_ref=anchor["anchor_ref"] if decision.value != "advance" else None,
        anchor_type=anchor["anchor_type"] if decision.value != "advance" else None,
        required_action_id=execution.action_id if decision.value == "repeat_retry" else None,
        required_book_id=getattr(execution.candidate, "book_id", None) if decision.value == "repeat_retry" else None,
    )


__all__ = ["compute_cycle_signals", "resolve_llm_vote", "run_reasoner_cycle"]
