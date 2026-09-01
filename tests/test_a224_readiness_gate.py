"""Tests for A224 Task 3: the Cynefin readiness gate pure function.

readiness_status() answers one question: is the graph ready for `resolve`
to commit to a goal, or should this cycle keep mapping entities instead?
Pure, no I/O -- mirrors transition()'s own "caller computes signals, this
function only decides" discipline. entity_domains is {entity_ref: CynefinDomain},
already computed by the caller via classify_domain() per entity.

Lives in Annatar's own module home deliberately -- A224's design conversation
corrected a first draft that proposed a separate, rival gate authority; this
is Annatar's own decision, not a new component (Shift B: single decision
owner, same anti-pattern A207's second_veto bug already burned this codebase
on once).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.annatar_state_machine import CynefinDomain, ReadinessStatus, readiness_status


class TestReadinessStatus:
    def test_all_entities_classified_is_ready(self):
        domains = {1: CynefinDomain.CONVERGED, 2: CynefinDomain.COMPLEX, 3: CynefinDomain.CHAOTIC}
        result = readiness_status(domains, step_index=1, max_cycles=30)
        assert result == ReadinessStatus.READY

    def test_one_disorder_entity_early_is_not_ready(self):
        domains = {1: CynefinDomain.CONVERGED, 2: CynefinDomain.DISORDER}
        result = readiness_status(domains, step_index=1, max_cycles=30)
        assert result == ReadinessStatus.NOT_READY

    def test_one_disorder_entity_past_budget_fraction_is_partial_fallthrough(self):
        # step_index=15 of max_cycles=30 -> exactly 50%, at the default
        # budget_fraction_before_fallthrough=0.5 boundary.
        domains = {1: CynefinDomain.CONVERGED, 2: CynefinDomain.DISORDER}
        result = readiness_status(domains, step_index=15, max_cycles=30)
        assert result == ReadinessStatus.PARTIAL_FALLTHROUGH

    def test_just_below_budget_fraction_is_still_not_ready(self):
        domains = {1: CynefinDomain.DISORDER}
        result = readiness_status(domains, step_index=14, max_cycles=30)
        assert result == ReadinessStatus.NOT_READY

    def test_no_entities_perceived_at_all_is_ready(self):
        """Defensible default, tested explicitly: nothing to map means
        nothing blocks proceeding -- a blank/empty grid shouldn't stall the
        episode forever waiting to map zero entities."""
        result = readiness_status({}, step_index=0, max_cycles=30)
        assert result == ReadinessStatus.READY

    def test_max_cycles_zero_does_not_crash(self):
        """Edge case: a degenerate budget must not raise ZeroDivisionError --
        with no budget at all, treat it as immediately past the fallthrough
        threshold."""
        domains = {1: CynefinDomain.DISORDER}
        result = readiness_status(domains, step_index=0, max_cycles=0)
        assert result == ReadinessStatus.PARTIAL_FALLTHROUGH

    def test_custom_budget_fraction_respected(self):
        domains = {1: CynefinDomain.DISORDER}
        result = readiness_status(
            domains, step_index=5, max_cycles=30, budget_fraction_before_fallthrough=0.1,
        )
        assert result == ReadinessStatus.PARTIAL_FALLTHROUGH


class TestReadinessStatusUntestedActions:
    """A231: readiness_status() also fires NOT_READY when whole-action-space
    coverage (fetch_untested_actions, A135) is incomplete, not just entity
    click-coverage."""

    def test_all_entities_classified_but_untested_action_remains_is_not_ready(self):
        domains = {1: CynefinDomain.CONVERGED, 2: CynefinDomain.COMPLEX, 3: CynefinDomain.CHAOTIC}
        result = readiness_status(
            domains, step_index=1, max_cycles=30, untested_non_click_actions=["ACTION3"],
        )
        assert result == ReadinessStatus.NOT_READY

    def test_no_entities_at_all_but_untested_action_remains_is_not_ready(self):
        """The pre-A231 empty-entity_domains early return must not shadow a
        genuinely untested action -- a blank/entity-free grid whose only
        real mechanic is a non-click action must still gate."""
        result = readiness_status(
            {}, step_index=1, max_cycles=30, untested_non_click_actions=["ACTION1"],
        )
        assert result == ReadinessStatus.NOT_READY

    def test_untested_action_past_budget_fraction_is_partial_fallthrough(self):
        domains = {1: CynefinDomain.CONVERGED}
        result = readiness_status(
            domains, step_index=15, max_cycles=30, untested_non_click_actions=["ACTION2"],
        )
        assert result == ReadinessStatus.PARTIAL_FALLTHROUGH

    def test_empty_untested_actions_default_is_ready_when_entities_all_classified(self):
        """Regression: the default `()` must behave exactly like the
        pre-A231 signature -- no untested_non_click_actions kwarg passed at
        all here, matching every pre-existing call site."""
        domains = {1: CynefinDomain.CONVERGED}
        result = readiness_status(domains, step_index=1, max_cycles=30)
        assert result == ReadinessStatus.READY
