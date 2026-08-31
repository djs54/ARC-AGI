"""Tests for A200: the pure investigation-thread state machine."""

from __future__ import annotations

import pytest

from agents.arc4.annatar_state_machine import (
    CycleSignals,
    CynefinDomain,
    InvestigationState,
    AnnatarDecision,
    AnnatarLimits,
    apply_llm_vote,
    decision_for_state,
    permissible_llm_transitions,
    transition,
)


def _signals(**overrides) -> CycleSignals:
    base = dict(
        meaningful_progress=False,
        confidence=0.0,
        untested_remaining=True,
        all_falsified=False,
        execution_inconclusive=False,
        deepening_cycle_count=0,
        already_retried=False,
    )
    base.update(overrides)
    return CycleSignals(**base)


class TestExploringTransitions:
    def test_partial_support_goes_to_deepening(self):
        signals = _signals(confidence=0.4)
        assert transition(InvestigationState.EXPLORING, signals) == InvestigationState.DEEPENING

    def test_strong_confirmation_goes_to_satisfied_via_confidence(self):
        signals = _signals(confidence=0.9)
        assert transition(InvestigationState.EXPLORING, signals) == InvestigationState.SATISFIED

    def test_meaningful_progress_goes_to_satisfied_regardless_of_confidence(self):
        signals = _signals(confidence=0.0, meaningful_progress=True)
        assert transition(InvestigationState.EXPLORING, signals) == InvestigationState.SATISFIED

    def test_all_falsified_alone_no_longer_exhausts(self):
        """A221 Finding 1: CycleSignals.all_falsified (check_stall, 100% local
        WorkflowState counters -- not graph-derived, see cycle_policy.py) no
        longer triggers EXHAUSTED on its own. Only a graph-grounded signal
        (CynefinDomain.CHAOTIC, see below) or the existing deepening_cycle_count
        ceiling may exit EXPLORING/DEEPENING now."""
        signals = _signals(all_falsified=True, untested_remaining=False)
        assert transition(InvestigationState.EXPLORING, signals) == InvestigationState.DEEPENING

    def test_falsified_but_untested_remain_does_not_exhaust(self):
        signals = _signals(all_falsified=True, untested_remaining=True)
        assert transition(InvestigationState.EXPLORING, signals) == InvestigationState.DEEPENING

    def test_chaotic_domain_goes_to_exhausted_from_exploring(self):
        """A221 Finding 1: classify_domain()'s CHAOTIC branch (all rule/
        hypothesis evidence for this anchor falsified, or A218's confirmed-
        inert-via-transition-history extension) IS graph-grounded -- unlike
        all_falsified, this is a legitimate "nothing left for this anchor"
        signal. Applies from EXPLORING too (not just DEEPENING): B369's
        thread-resume means an anchor can reopen already knowing its own
        history, no wasted cycle needed before exiting on it."""
        signals = _signals(domain=CynefinDomain.CHAOTIC)
        assert transition(InvestigationState.EXPLORING, signals) == InvestigationState.EXHAUSTED

    def test_chaotic_domain_overrides_untested_remaining(self):
        """Unlike the old all_falsified check (gated on `not untested_remaining`),
        CHAOTIC is anchor-specific: other action families being untested
        elsewhere doesn't make THIS anchor's confirmed-dead evidence any less
        dead. decision_for_state(EXHAUSTED) -> ADVANCE picks a fresh anchor
        next cycle, where untested_remaining's information is used instead."""
        signals = _signals(domain=CynefinDomain.CHAOTIC, untested_remaining=True)
        assert transition(InvestigationState.EXPLORING, signals) == InvestigationState.EXHAUSTED

    def test_inconclusive_execution_goes_to_retry(self):
        signals = _signals(execution_inconclusive=True)
        assert transition(InvestigationState.EXPLORING, signals) == InvestigationState.RETRY

    def test_known_chaotic_anchor_skips_retry_goes_straight_to_exhausted(self):
        """A221 Finding 5: a known-CHAOTIC anchor (e.g. resumed via B369's
        thread-resume with prior confirmed-dead history) shouldn't waste a
        RETRY cycle just because this cycle's own click also happened to be
        inconclusive -- the graph already answered the question RETRY exists
        to explore. Mirrors Finding 1's "no wasted cycle" reasoning, applied
        to this second exit-to-EXHAUSTED path."""
        signals = _signals(execution_inconclusive=True, domain=CynefinDomain.CHAOTIC)
        assert transition(InvestigationState.EXPLORING, signals) == InvestigationState.EXHAUSTED

    def test_meaningful_progress_wins_even_if_domain_reads_stale_chaotic(self):
        """Safety property: the CHAOTIC-skips-RETRY check must never be able
        to steal a legitimate SATISFIED outcome. In real production code
        execution_inconclusive=True guarantees meaningful_progress=False (see
        annatar_signals.py::compute_cycle_signals's own construction), but
        this test constructs the "impossible" combination directly against
        transition() in isolation to prove the ordering is safe on its own
        terms, not just safe because of an invariant enforced elsewhere."""
        signals = _signals(execution_inconclusive=True, domain=CynefinDomain.CHAOTIC, meaningful_progress=True)
        assert transition(InvestigationState.EXPLORING, signals) == InvestigationState.SATISFIED


class TestDeepeningTransitions:
    def test_confidence_threshold_crossed_goes_to_satisfied(self):
        signals = _signals(confidence=0.8)
        assert transition(InvestigationState.DEEPENING, signals) == InvestigationState.SATISFIED

    def test_all_falsified_alone_no_longer_exhausts(self):
        """A221 Finding 1: see the EXPLORING-side test of the same name."""
        signals = _signals(all_falsified=True, untested_remaining=False)
        assert transition(InvestigationState.DEEPENING, signals) == InvestigationState.DEEPENING

    def test_chaotic_domain_goes_to_exhausted_from_deepening(self):
        signals = _signals(domain=CynefinDomain.CHAOTIC, deepening_cycle_count=1)
        assert transition(InvestigationState.DEEPENING, signals) == InvestigationState.EXHAUSTED

    def test_still_ambiguous_below_llm_threshold_stays_deepening(self):
        signals = _signals(deepening_cycle_count=1)
        assert transition(InvestigationState.DEEPENING, signals) == InvestigationState.DEEPENING

    def test_ambiguous_at_llm_threshold_escalates(self):
        signals = _signals(deepening_cycle_count=3)
        assert transition(InvestigationState.DEEPENING, signals) == InvestigationState.AWAITING_LLM

    def test_custom_limits_respected(self):
        signals = _signals(deepening_cycle_count=1)
        limits = AnnatarLimits(max_deepening_cycles_before_llm=1)
        assert transition(InvestigationState.DEEPENING, signals, limits) == InvestigationState.AWAITING_LLM


class TestRetryTransitions:
    def test_fresh_result_reevaluates_as_exploring(self):
        signals = _signals(execution_inconclusive=False, confidence=0.9)
        assert transition(InvestigationState.RETRY, signals) == InvestigationState.SATISFIED

    def test_second_inconclusive_result_exhausts_not_retries_again(self):
        signals = _signals(execution_inconclusive=True, already_retried=True)
        assert transition(InvestigationState.RETRY, signals) == InvestigationState.EXHAUSTED

    def test_second_inconclusive_result_on_complex_domain_falls_through_to_deepening(self):
        """A221 Finding 5: a COMPLEX anchor (genuine, live, disagreeing
        evidence -- the domain complex_domain_deepening_multiplier exists
        specifically to protect) shouldn't be killed by two raw inconclusive
        clicks. Falls through to the same EXPLORING/DEEPENING patience logic
        instead of a hard EXHAUSTED -- deepening_cycle_count=0 here confirms
        it lands on DEEPENING, not immediately re-triggering RETRY (already_
        retried=True blocks that) or wrongly escalating."""
        signals = _signals(
            execution_inconclusive=True, already_retried=True,
            domain=CynefinDomain.COMPLEX, deepening_cycle_count=0,
        )
        assert transition(InvestigationState.RETRY, signals) == InvestigationState.DEEPENING

    def test_second_inconclusive_result_on_non_complex_domain_still_exhausts(self):
        """Regression: the COMPLEX carve-out is specific to COMPLEX, not a
        general softening of RETRY's exhaustion -- DISORDER (the default,
        no special evidence) still exhausts exactly as before."""
        signals = _signals(
            execution_inconclusive=True, already_retried=True,
            domain=CynefinDomain.DISORDER,
        )
        assert transition(InvestigationState.RETRY, signals) == InvestigationState.EXHAUSTED


class TestAwaitingLlmGuard:
    def test_transition_rejects_awaiting_llm_as_current_state(self):
        signals = _signals()
        with pytest.raises(ValueError, match="apply_llm_vote"):
            transition(InvestigationState.AWAITING_LLM, signals)


class TestPermissibleLlmTransitions:
    def test_exhausted_not_permitted_when_untested_remain(self):
        signals = _signals(untested_remaining=True, all_falsified=False)
        assert InvestigationState.EXHAUSTED not in permissible_llm_transitions(signals)

    def test_exhausted_permitted_when_nothing_untested_and_all_falsified(self):
        signals = _signals(untested_remaining=False, all_falsified=True)
        assert InvestigationState.EXHAUSTED in permissible_llm_transitions(signals)

    def test_deepening_and_satisfied_always_permitted(self):
        signals = _signals()
        permitted = permissible_llm_transitions(signals)
        assert InvestigationState.DEEPENING in permitted
        assert InvestigationState.SATISFIED in permitted


class TestApplyLlmVote:
    def test_in_set_vote_is_honored(self):
        signals = _signals()
        assert apply_llm_vote(InvestigationState.SATISFIED, signals) == InvestigationState.SATISFIED

    def test_out_of_set_vote_falls_back_to_exhausted_when_permitted(self):
        signals = _signals(untested_remaining=False, all_falsified=True)
        # EXPLORING is never in permissible_llm_transitions' output
        assert apply_llm_vote(InvestigationState.EXPLORING, signals) == InvestigationState.EXHAUSTED

    def test_out_of_set_vote_falls_back_to_deepening_when_exhausted_not_permitted(self):
        signals = _signals(untested_remaining=True, all_falsified=False)
        assert apply_llm_vote(InvestigationState.EXPLORING, signals) == InvestigationState.DEEPENING


class TestDecisionForState:
    def test_satisfied_maps_to_advance_not_terminate(self):
        assert decision_for_state(InvestigationState.SATISFIED) == AnnatarDecision.ADVANCE

    def test_exhausted_maps_to_advance_not_terminate(self):
        assert decision_for_state(InvestigationState.EXHAUSTED) == AnnatarDecision.ADVANCE

    def test_retry_maps_to_repeat_retry(self):
        assert decision_for_state(InvestigationState.RETRY) == AnnatarDecision.REPEAT_RETRY

    def test_deepening_maps_to_repeat_deepen(self):
        assert decision_for_state(InvestigationState.DEEPENING) == AnnatarDecision.REPEAT_DEEPEN

    def test_exploring_maps_to_repeat_deepen(self):
        assert decision_for_state(InvestigationState.EXPLORING) == AnnatarDecision.REPEAT_DEEPEN

    def test_awaiting_llm_has_no_decision_mapping(self):
        with pytest.raises(ValueError, match="no decision mapping"):
            decision_for_state(InvestigationState.AWAITING_LLM)
