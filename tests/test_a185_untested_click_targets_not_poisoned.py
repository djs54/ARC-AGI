"""Tests for A185: plan_generator.py computed the graph's falsification-
contradiction penalty once per action_id and reused it as the base score for
every ACTION6 click-target coordinate, so a never-before-clicked coordinate
inherited OTHER coordinates' failure history as its own starting penalty.
Confirmed live 2026-08-08 (sk48-d8078629): three genuinely untested
coordinates scored -4.58 each (worse than any actually-falsified action)
while their own rationale said "untested". Fix: the family-level
contradiction penalty is withheld for a click-target book_id that has never
itself been attempted; the positive family-wide signal still transfers.
See backlog/A185.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.types import GoalHypothesis, PerceivedEntity, PerceptionSnapshot, ResolvedGoal, WorkflowState


class _GraphPort:
    def __init__(self, evidence: dict[str, Any] | None = None, rules: list[dict[str, Any]] | None = None) -> None:
        self._evidence = evidence or {"supports": 0, "contradictions": 0, "confidence": 0.0, "attempts": 0}
        self._rules = rules or []

    def fetch_goal_evidence(self, perception, goal=None):
        return {}

    def fetch_per_action_evidence(self, action_id: str) -> dict[str, Any]:
        return dict(self._evidence)

    def fetch_rules_for_action(self, action_id: str) -> list[dict[str, Any]]:
        return list(self._rules)


def _click_entity(value: str, row: float, col: float, cell_count: int = 5) -> PerceivedEntity:
    return PerceivedEntity(
        kind="point",
        value=value,
        attributes={
            "coverage": 0.01,
            "cell_count": cell_count,
            "centroid": (row, col),
        },
    )


def _goal() -> ResolvedGoal:
    return ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="test goal", confidence=0.8))


def _state() -> WorkflowState:
    return WorkflowState(step_index=0, action_attempt_counts={}, action_falsification_counts={}, consecutive_no_progress_count=0)


LIMITS = PlanGeneratorLimits()


class TestUntestedClickTargetNotPoisonedByFamilyContradictions:
    def test_fresh_click_target_score_matches_live_reproduction(self):
        """This card's own reproduction: heavy family-level contradictions
        (contradictions=9, supports=0, matching the live sk48 episode) must
        not drag a never-before-clicked coordinate below the untested
        baseline."""
        graph = _GraphPort(evidence={"supports": 0, "contradictions": 9, "confidence": 0.0, "attempts": 9})
        planner = PlanGenerator(LIMITS)
        state = _state()
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION6"]},
            grid_hash="hash-1",
            entities=(_click_entity("1", 10, 20),),
        )

        result = planner.generate(state, perception, _goal(), graph_port=graph).payload

        assert str(result.candidate.metadata.get("book_id", "")).startswith("ACTION6@")
        assert result.candidate.score >= 0.0, (
            f"untested click target should not be poisoned by unrelated family contradictions, "
            f"got score={result.candidate.score}"
        )
        assert result.candidate.score == pytest.approx(LIMITS.untested_bonus)

    def test_old_formula_would_have_produced_deeply_negative_score(self):
        """Sanity check that this scenario genuinely exercises the bug: the
        withheld penalty (0.16 * 9 = 1.44) would have made the old combined
        formula deeply negative, matching the live -4.58 order of magnitude
        once goal_alignment/voi_bonus were folded in."""
        graph_contradiction_penalty = LIMITS.falsification_penalty * 9
        assert graph_contradiction_penalty > LIMITS.untested_bonus


class TestPositiveFamilySignalStillTransfers:
    def test_positive_evidence_confidence_still_boosts_untested_click_target(self):
        graph = _GraphPort(evidence={"supports": 3, "contradictions": 0, "confidence": 0.6, "attempts": 3})
        planner = PlanGenerator(LIMITS)
        state = _state()
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION6"]},
            grid_hash="hash-1",
            entities=(_click_entity("1", 10, 20),),
        )

        result = planner.generate(state, perception, _goal(), graph_port=graph).payload

        assert result.candidate.score > LIMITS.untested_bonus

    def test_live_rule_confidence_still_boosts_untested_click_target(self):
        graph = _GraphPort(rules=[{"rule_id": "r1", "confidence": 0.8, "falsified": False}])
        planner = PlanGenerator(LIMITS)
        state = _state()
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION6"]},
            grid_hash="hash-1",
            entities=(_click_entity("1", 10, 20),),
        )

        result = planner.generate(state, perception, _goal(), graph_port=graph).payload

        assert result.candidate.score > LIMITS.untested_bonus


class TestAlreadyAttemptedClickTargetStillPenalized:
    def test_repeated_click_target_is_excluded_not_merely_penalized(self):
        """Regression guard: this card must not weaken A182's fix -- a
        book_id that HAS itself been attempted and falsified twice must not
        be let back in.

        Superseded by A191 (2026-08-23): repeated_falsified book_ids are now
        excluded from the candidate set entirely at construction, a strictly
        stronger guarantee than "penalized" -- this test now asserts
        exclusion instead of a negative score (the original scenario, a
        single available action with falsifications=2 and no alternatives).

        Superseded again by A238 (2026-09-01): the excluded-book_id case
        used to fall back to a synthetic "probe-*" candidate that was never
        a real ARC command and guaranteed a 404 at the live transport
        (backlog/A238.md). It now produces no candidate at all
        (PlanningResult.candidate is None), so this test asserts that
        directly instead of asserting on a fallback book_id that no longer
        exists -- either way, ACTION6@20,10 itself is never proposed."""
        graph = _GraphPort(evidence={"supports": 0, "contradictions": 2, "confidence": 0.0, "attempts": 2})
        planner = PlanGenerator(LIMITS)
        state = WorkflowState(
            step_index=2,
            action_attempt_counts={"ACTION6@20,10": 2},
            action_falsification_counts={"ACTION6@20,10": 2},
            consecutive_no_progress_count=0,
        )
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION6"]},
            grid_hash="hash-1",
            entities=(_click_entity("1", 10, 20),),
        )

        result = planner.generate(state, perception, _goal(), graph_port=graph).payload

        assert result.candidate is None, (
            "a click target that has itself been falsified twice must be excluded from the "
            "candidate set entirely (A191); with no alternative available, A238 leaves "
            "PlanningResult.candidate as None rather than inventing a doomed fallback"
        )


class TestNonClickActionsUnaffected:
    def test_untested_non_click_action_still_uses_family_evidence(self):
        """Regression guard: non-ACTION6 actions have book_id == action_id
        always, so the family-level evidence IS this action's own evidence
        (e.g. persisted cross-session history) and must still apply even on
        a fresh-this-episode attempt -- this card's withholding is scoped to
        the click-target case only."""
        graph = _GraphPort(evidence={"supports": 0, "contradictions": 5, "confidence": 0.0, "attempts": 5})
        planner = PlanGenerator(LIMITS)
        state = _state()
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION1"]},
            grid_hash="hash-1",
        )

        result = planner.generate(state, perception, _goal(), graph_port=graph).payload

        assert result.candidate.action_id == "ACTION1"
        expected = LIMITS.untested_bonus - LIMITS.falsification_penalty * 5
        assert result.candidate.score == pytest.approx(expected)
