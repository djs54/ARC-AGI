"""A239: `_choose_alternative`'s exclusion check (and the `latest_veto_alternative`
fallback check) must key on book_id, not the family-level action_id.

A188 (2026-08-18) fixed this function's attempt-count *lookup* to key by
book_id, but left the *exclusion* check comparing action_id one line above
it. For ACTION6, every click candidate shares the bare action_id "ACTION6"
regardless of coordinate (A185) -- so whenever the vetoed candidate was
itself ACTION6, every other ACTION6 alternative got skipped by the
exclusion check before its (correctly book_id-keyed) attempt count was ever
examined, no matter how fresh that alternative's own coordinate was.

Kept as a separate file from tests/test_a188_vetter_keys_by_book_id.py
rather than extended in place: that file's class is literally named/scoped
to "keys by book_id" (the attempt-count *lookup* A188 fixed), and every one
of its `_choose_alternative`-adjacent cases deliberately pairs ACTION6
against ACTION1 -- the one pairing where the exclusion bug this card fixes
never mattered (see A239's card, "Why A188's own test suite didn't catch
this"). This file is scoped to the exclusion check itself (ACTION6 vs a
*different* ACTION6 coordinate), a related but distinct behavior, so
keeping the files separate keeps each one's scope traceable to its own
card without retroactively widening A188's docstring/class name to cover
behavior it didn't actually fix.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.plan_vetter import PlanVetter, PlanVetterLimits
from agents.arc4.types import (
    PerceptionSnapshot,
    PlanCandidate,
    PlanningResult,
    GoalHypothesis,
    ResolvedGoal,
    WorkflowState,
)


def _state(**overrides) -> WorkflowState:
    defaults = dict(
        step_index=0,
        action_attempt_counts={},
        action_falsification_counts={},
        consecutive_no_progress_count=0,
    )
    defaults.update(overrides)
    return WorkflowState(**defaults)


def _perception() -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"grid": "hash-1", "available_actions": ["ACTION6"]},
        grid_hash="hash-1",
    )


def _goal() -> ResolvedGoal:
    return ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="test goal", confidence=0.8))


def _click_candidate(book_id: str, score: float = -1.0) -> PlanCandidate:
    return PlanCandidate(action_id="ACTION6", goal_id="goal-1", score=score, metadata={"book_id": book_id})


class TestA239ChooseAlternativeExcludesByBookIdNotActionId:
    def test_repeated_falsification_veto_offers_fresh_click_alternative(self):
        """The card's exact live reproduction: a twice-falsified ACTION6@10,10 candidate
        with a genuinely fresh ACTION6@20,20 alternative in plan.alternatives. Before the
        fix: the action_id exclusion discarded the fresh alternative (same action_id
        "ACTION6"), _choose_alternative returned None, and the veto never fired --
        approved=True despite two falsifications on record. After the fix: the veto fires
        and the fresh alternative is offered."""
        state = _state(
            action_attempt_counts={"ACTION6@10,10": 3},
            action_falsification_counts={"ACTION6@10,10": 2},
        )
        vetoed_candidate = _click_candidate("ACTION6@10,10", score=-1.0)
        fresh_alt = _click_candidate("ACTION6@20,20", score=0.2)
        plan = PlanningResult(candidate=vetoed_candidate, alternatives=(fresh_alt,))
        vetter = PlanVetter(PlanVetterLimits(repeated_falsification_threshold=2))
        result = vetter.vet(state, _perception(), _goal(), plan)
        assert result.payload.approved is False
        assert result.payload.metadata["veto_type"] == "repeated_falsification"
        assert result.payload.alternative is fresh_alt

    def test_excessive_repetition_veto_offers_fresh_click_alternative(self):
        """Same shape as the card's repro, but for the excessive_repetition_threshold
        path: candidate attempted >= threshold with weak score. Before the fix, the
        fresh ACTION6 alternative was discarded by the action_id exclusion and the veto
        could not fire for lack of an alternative."""
        state = _state(action_attempt_counts={"ACTION6@10,10": 3})
        vetoed_candidate = _click_candidate("ACTION6@10,10", score=0.1)
        fresh_alt = _click_candidate("ACTION6@20,20", score=0.2)
        plan = PlanningResult(candidate=vetoed_candidate, alternatives=(fresh_alt,))
        vetter = PlanVetter(PlanVetterLimits(excessive_repetition_threshold=3, weak_evidence_score_threshold=0.4))
        result = vetter.vet(state, _perception(), _goal(), plan)
        assert result.payload.approved is False
        assert result.payload.metadata["veto_type"] == "excessive_repetition"
        assert result.payload.alternative is fresh_alt

    def test_candidate_never_offered_as_alternative_to_itself(self):
        """Regression guard: this must survive the fix, not just be an accident of the
        old code. Construct alternatives containing an entry with the exact same
        book_id as the candidate (e.g. a duplicate/re-scored copy of the same
        coordinate) plus a genuinely different fresh coordinate -- _choose_alternative
        must skip the same-book_id entry and offer the different one instead."""
        state = _state()
        candidate = _click_candidate("ACTION6@10,10", score=-1.0)
        same_book_id_duplicate = _click_candidate("ACTION6@10,10", score=0.9)  # same coordinate, re-scored
        fresh_alt = _click_candidate("ACTION6@20,20", score=0.2)
        plan = PlanningResult(candidate=candidate, alternatives=(same_book_id_duplicate, fresh_alt))
        vetter = PlanVetter(PlanVetterLimits())
        alternative = vetter._choose_alternative(state, candidate, plan.alternatives)
        assert alternative is fresh_alt
        assert alternative is not same_book_id_duplicate

    def test_candidate_never_offered_as_alternative_to_itself_when_it_is_the_only_option(self):
        """If the only 'alternative' present shares the candidate's own book_id, no
        alternative should be returned at all -- not the self-duplicate."""
        state = _state()
        candidate = _click_candidate("ACTION6@10,10", score=-1.0)
        same_book_id_duplicate = _click_candidate("ACTION6@10,10", score=0.9)
        vetter = PlanVetter(PlanVetterLimits())
        alternative = vetter._choose_alternative(state, candidate, (same_book_id_duplicate,))
        assert alternative is None

    def test_latest_veto_alternative_fallback_offers_fresh_click_alternative(self):
        """The state.latest_veto_alternative fallback path: plan.alternatives offers
        nothing usable (empty), but state.latest_veto_alternative is a fresh ACTION6
        coordinate different from the vetoed candidate's own coordinate. Before the fix,
        the action_id comparison on this fallback check also discarded it (same
        action_id "ACTION6"). After the fix, it's correctly offered."""
        fresh_veto_alt = _click_candidate("ACTION6@30,30", score=0.15)
        state = _state(latest_veto_alternative=fresh_veto_alt)
        vetoed_candidate = _click_candidate("ACTION6@10,10", score=-1.0)
        plan = PlanningResult(candidate=vetoed_candidate, alternatives=())
        vetter = PlanVetter(PlanVetterLimits())
        alternative = vetter._choose_alternative(state, vetoed_candidate, plan.alternatives)
        assert alternative is fresh_veto_alt

    def test_latest_veto_alternative_fallback_still_excludes_same_book_id(self):
        """The latest_veto_alternative fallback must still refuse to offer the exact
        same coordinate as the vetoed candidate, even though book_id (not action_id) is
        now the comparison key."""
        same_coordinate_veto_alt = _click_candidate("ACTION6@10,10", score=0.15)
        state = _state(latest_veto_alternative=same_coordinate_veto_alt)
        vetoed_candidate = _click_candidate("ACTION6@10,10", score=-1.0)
        plan = PlanningResult(candidate=vetoed_candidate, alternatives=())
        vetter = PlanVetter(PlanVetterLimits())
        alternative = vetter._choose_alternative(state, vetoed_candidate, plan.alternatives)
        assert alternative is None
