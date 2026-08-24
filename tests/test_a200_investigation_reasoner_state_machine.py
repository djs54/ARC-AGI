"""Tests for A200: the pure investigation-thread state machine."""

from __future__ import annotations

import pytest

from agents.arc4.investigation_reasoner import (
    CycleSignals,
    InvestigationState,
    ReasonerDecision,
    ReasonerLimits,
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

    def test_immediately_falsified_no_alternatives_goes_to_exhausted(self):
        signals = _signals(all_falsified=True, untested_remaining=False)
        assert transition(InvestigationState.EXPLORING, signals) == InvestigationState.EXHAUSTED

    def test_falsified_but_untested_remain_does_not_exhaust(self):
        signals = _signals(all_falsified=True, untested_remaining=True)
        assert transition(InvestigationState.EXPLORING, signals) == InvestigationState.DEEPENING

    def test_inconclusive_execution_goes_to_retry(self):
        signals = _signals(execution_inconclusive=True)
        assert transition(InvestigationState.EXPLORING, signals) == InvestigationState.RETRY


class TestDeepeningTransitions:
    def test_confidence_threshold_crossed_goes_to_satisfied(self):
        signals = _signals(confidence=0.8)
        assert transition(InvestigationState.DEEPENING, signals) == InvestigationState.SATISFIED

    def test_all_falsified_no_untested_goes_to_exhausted(self):
        signals = _signals(all_falsified=True, untested_remaining=False)
        assert transition(InvestigationState.DEEPENING, signals) == InvestigationState.EXHAUSTED

    def test_still_ambiguous_below_llm_threshold_stays_deepening(self):
        signals = _signals(deepening_cycle_count=1)
        assert transition(InvestigationState.DEEPENING, signals) == InvestigationState.DEEPENING

    def test_ambiguous_at_llm_threshold_escalates(self):
        signals = _signals(deepening_cycle_count=3)
        assert transition(InvestigationState.DEEPENING, signals) == InvestigationState.AWAITING_LLM

    def test_custom_limits_respected(self):
        signals = _signals(deepening_cycle_count=1)
        limits = ReasonerLimits(max_deepening_cycles_before_llm=1)
        assert transition(InvestigationState.DEEPENING, signals, limits) == InvestigationState.AWAITING_LLM


class TestRetryTransitions:
    def test_fresh_result_reevaluates_as_exploring(self):
        signals = _signals(execution_inconclusive=False, confidence=0.9)
        assert transition(InvestigationState.RETRY, signals) == InvestigationState.SATISFIED

    def test_second_inconclusive_result_exhausts_not_retries_again(self):
        signals = _signals(execution_inconclusive=True, already_retried=True)
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
        assert decision_for_state(InvestigationState.SATISFIED) == ReasonerDecision.ADVANCE

    def test_exhausted_maps_to_advance_not_terminate(self):
        assert decision_for_state(InvestigationState.EXHAUSTED) == ReasonerDecision.ADVANCE

    def test_retry_maps_to_repeat_retry(self):
        assert decision_for_state(InvestigationState.RETRY) == ReasonerDecision.REPEAT_RETRY

    def test_deepening_maps_to_repeat_deepen(self):
        assert decision_for_state(InvestigationState.DEEPENING) == ReasonerDecision.REPEAT_DEEPEN

    def test_exploring_maps_to_repeat_deepen(self):
        assert decision_for_state(InvestigationState.EXPLORING) == ReasonerDecision.REPEAT_DEEPEN

    def test_awaiting_llm_has_no_decision_mapping(self):
        with pytest.raises(ValueError, match="no decision mapping"):
            decision_for_state(InvestigationState.AWAITING_LLM)
