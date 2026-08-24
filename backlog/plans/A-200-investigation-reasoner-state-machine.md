# Plan: A200 — Investigation-Thread State Machine

## Card metadata

- ID: A200
- Priority: P1
- Layer: ARC runtime
- Dependencies: None

## Summary

Implement the pure state machine from `docs/superpowers/specs/2026-08-23-trajectory-reasoner-design.md` §4 as a standalone module, `agents/arc4/investigation_reasoner.py`, mirroring `agents/arc4/cycle_policy.py`'s style exactly (pure functions, stdlib-only, deterministic, fully unit-testable with no graph/LLM/I/O). Read that spec file in full before starting.

## Technical approach

Read `agents/arc4/cycle_policy.py` in full first — this card's module must match its style (module docstring, `dataclass(slots=True)` where state is needed, plain functions, an `__all__` export list at the bottom).

### 1. `agents/arc4/investigation_reasoner.py`

```python
"""Pure investigation-thread state machine for the trajectory Reasoner
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


class ReasonerDecision(StrEnum):
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


@dataclass(slots=True)
class ReasonerLimits:
    """Starting-point thresholds, no empirical basis yet -- see spec section
    11. Tune with real data once this lands, don't treat these as final."""

    satisfied_confidence_threshold: float = 0.75
    max_deepening_cycles_before_llm: int = 3


def transition(
    current_state: InvestigationState,
    signals: CycleSignals,
    limits: ReasonerLimits | None = None,
) -> InvestigationState:
    """Deterministic transition table, spec section 4.2. Does not resolve
    AWAITING_LLM -- that's apply_llm_vote's job, called separately by the
    integration layer once an LLM answer is available."""
    limits = limits or ReasonerLimits()

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


def decision_for_state(new_state: InvestigationState) -> ReasonerDecision:
    """Map a resolved investigation-thread state to what the orchestrator
    does next. SATISFIED/EXHAUSTED end THIS thread, not the episode -- both
    map to ADVANCE (start a fresh thread on a new anchor). Whole-episode
    TERMINATE is decided by the integration layer (A202), which alone knows
    whether there's anything left to advance to -- never by this function."""
    if new_state in (InvestigationState.SATISFIED, InvestigationState.EXHAUSTED):
        return ReasonerDecision.ADVANCE
    if new_state == InvestigationState.RETRY:
        return ReasonerDecision.REPEAT_RETRY
    if new_state in (InvestigationState.DEEPENING, InvestigationState.EXPLORING):
        return ReasonerDecision.REPEAT_DEEPEN
    raise ValueError(f"no decision mapping for state: {new_state}")


__all__ = [
    "InvestigationState",
    "ReasonerDecision",
    "CycleSignals",
    "ReasonerLimits",
    "transition",
    "permissible_llm_transitions",
    "apply_llm_vote",
    "decision_for_state",
]
```

### 2. Helper for tests

Add a small factory in the test file (not the module itself) for building `CycleSignals` with sensible all-False/zero defaults, so each test only sets the one or two fields it cares about:

```python
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
```

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/investigation_reasoner.py` (new) | State machine, per code above |
| `tests/test_a200_investigation_reasoner_state_machine.py` (new) | Coverage, see Tests |

## Tests

Write these test-first (TDD): write the test, run it, confirm it fails for the right reason (module doesn't exist yet), then implement, then confirm it passes.

`tests/test_a200_investigation_reasoner_state_machine.py`:

```python
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
```

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a200_investigation_reasoner_state_machine.py -v
grep -n "^import\|^from" agents/arc4/investigation_reasoner.py
make test-a
make test-all
```

The `grep` is the "zero I/O dependency" acceptance-criterion checkpoint — it must show only `from __future__ import annotations`, `from dataclasses import dataclass`, `from enum import StrEnum`.

## Assumptions/defaults

- `ReasonerLimits`' two default values (`0.75`, `3`) are explicitly starting points per the spec's own §11 — do not treat them as tuned; the acceptance criteria don't require any specific value, only that they exist and are overridable.
- This module is deliberately inert on its own — it produces no behavior change anywhere in the runtime until A202 wires it into `WorkflowOrchestrator`. That's expected; don't add integration code here.
