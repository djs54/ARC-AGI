"""Tests for A187: plan_generator.py multiplied the *combined* score
(graph_positive_score - graph_contradiction_penalty) by
repeat_decay_factor ** attempts. Applied to a negative value, this shrinks
the falsification penalty toward zero as attempts grow -- confirmed live
2026-08-08 (tu93-0768757b): an action falsified 4 times outscored actions
falsified only once each (-0.2455 vs -0.2680). Fix: only graph_positive_score
is decayed; graph_contradiction_penalty is subtracted afterward at full,
undecayed magnitude. See backlog/A187.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.types import GoalHypothesis, PerceptionSnapshot, ResolvedGoal, WorkflowState

LIMITS = PlanGeneratorLimits()


class _GraphPort:
    def __init__(self, contradictions: int) -> None:
        self._contradictions = contradictions

    def fetch_goal_evidence(self, perception, goal=None):
        return {}

    def fetch_per_action_evidence(self, action_id: str) -> dict[str, Any]:
        return {"supports": 0, "contradictions": self._contradictions, "confidence": 0.0, "attempts": self._contradictions}


def _goal() -> ResolvedGoal:
    return ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="test goal", confidence=0.8))


def _perception(action_id: str = "ACTION1") -> PerceptionSnapshot:
    return PerceptionSnapshot(observation={"grid": "hash-1", "available_actions": [action_id]}, grid_hash="hash-1")


def _score_after_n_falsifications(action_id: str, n: int) -> float:
    """Reproduces this card's live scenario: an action attempted+falsified
    n times, with the graph's own contradiction evidence growing to match
    (mirrors real play, where each real failure adds one more contradiction
    server-side)."""
    graph = _GraphPort(contradictions=n)
    planner = PlanGenerator(LIMITS)
    state = WorkflowState(
        step_index=n,
        action_attempt_counts={action_id: n},
        action_falsification_counts={action_id: n},
        consecutive_no_progress_count=0,
    )
    result = planner.generate(state, _perception(action_id), _goal(), graph_port=graph).payload
    return result.candidate.score


def _candidate_after_n_falsifications(action_id: str, n: int):
    """Same scenario as _score_after_n_falsifications, but returns the raw
    candidate (possibly None) instead of assuming one exists -- A238 made
    PlanningResult.candidate=None a real, reachable outcome once the only
    available action is excluded and no alternative exists."""
    graph = _GraphPort(contradictions=n)
    planner = PlanGenerator(LIMITS)
    state = WorkflowState(
        step_index=n,
        action_attempt_counts={action_id: n},
        action_falsification_counts={action_id: n},
        consecutive_no_progress_count=0,
    )
    result = planner.generate(state, _perception(action_id), _goal(), graph_port=graph).payload
    return result.candidate


class TestDecayNoLongerForgivesRepeatedFalsification:
    """Updated for A191 (2026-08-23): this class's original concern was that
    decay could make a repeatedly-falsified action's score improve instead
    of worsen as failures accumulated -- verified by comparing scores across
    falsification counts 1/2/3/4. A191 makes that comparison moot for
    falsifications >= 2: such actions are now excluded from the candidate
    set entirely (never scored at all), a strictly stronger guarantee than
    "scored correctly and worsening." The tests below are restructured to
    verify: (1) exclusion now holds for every falsification count this
    card's original scenario covered, and (2) the one falsification count
    still reachable through scoring (1, below A191's exclusion threshold)
    still exercises this card's actual decay-math fix correctly."""

    def test_repeated_falsification_is_excluded_not_merely_scored(self):
        """A191 supersedes this card's original "does it get worse" concern
        for falsifications >= 2 -- confirm exclusion holds across the whole
        range this card's live finding covered (2, 3, and 4 failures), not
        just the boundary.

        Updated again for A238 (2026-09-01): ACTION1 is the only available
        action here, so once A191 excludes it there is nothing left to
        propose. This used to fall back to a synthetic "probe-*" candidate
        with a flat 0.1 score -- that candidate was never a real ARC command
        and guaranteed a 404 at the live transport (backlog/A238.md). The
        fix leaves PlanningResult.candidate as None instead, so this test
        now asserts that directly rather than asserting on a fallback score
        that no longer exists."""
        for n in (2, 3, 4):
            candidate = _candidate_after_n_falsifications("ACTION1", n)
            assert candidate is None, (
                f"ACTION1 falsified {n} times should be excluded from real candidates, "
                f"and with no alternative available should leave no candidate at all "
                f"(A238), got {candidate}"
            )

    def test_single_falsification_still_penalizes_correctly(self):
        """The one falsification count below A191's exclusion threshold
        (falsifications=1) still exercises this card's actual fix directly:
        the contradiction penalty must be subtracted at full, undecayed
        magnitude, not swallowed by repeat_decay_factor ** attempts."""
        score = _score_after_n_falsifications("ACTION1", 1)

        decay = LIMITS.repeat_decay_factor**1
        graph_contradiction_penalty = LIMITS.falsification_penalty * 1
        expected = 0.0 * decay - graph_contradiction_penalty - min(LIMITS.repeat_attempt_penalty * 1, 0.18)
        assert score == pytest.approx(expected)
        assert score < 0, "a single real falsification must still be penalized, not neutral or positive"


class TestPositiveSignalStillDecays:
    def test_positive_family_evidence_fades_with_repeated_attempts(self):
        """Regression guard: this card must not remove decay entirely --
        a genuinely positive family signal (no contradictions) should still
        fade as the same action is repeated, exactly as before."""

        class _PositiveGraphPort:
            def fetch_goal_evidence(self, perception, goal=None):
                return {}

            def fetch_per_action_evidence(self, action_id: str) -> dict[str, Any]:
                return {"supports": 5, "contradictions": 0, "confidence": 0.6, "attempts": 5}

        planner = PlanGenerator(LIMITS)

        def score_at(attempts: int) -> float:
            state = WorkflowState(
                step_index=attempts,
                action_attempt_counts={"ACTION1": attempts},
                action_falsification_counts={},
                consecutive_no_progress_count=0,
            )
            return planner.generate(state, _perception(), _goal(), graph_port=_PositiveGraphPort()).payload.candidate.score

        score_1 = score_at(1)
        score_3 = score_at(3)
        assert score_3 < score_1, "a positive family signal should still fade (decay) with repeated attempts"


class TestUntestedPathUnaffected:
    def test_first_attempt_scoring_unchanged(self):
        """Regression guard: this card only touches the tested (attempts>0)
        branch -- a genuinely first attempt must be unaffected."""
        graph = _GraphPort(contradictions=0)
        planner = PlanGenerator(LIMITS)
        state = WorkflowState(step_index=0, action_attempt_counts={}, action_falsification_counts={}, consecutive_no_progress_count=0)

        result = planner.generate(state, _perception(), _goal(), graph_port=graph).payload

        assert result.candidate.score == pytest.approx(LIMITS.untested_bonus)
