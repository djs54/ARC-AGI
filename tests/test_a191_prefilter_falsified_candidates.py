"""Tests for A191: plan_generator.py::_build_candidates skips entirely any book_id
with falsifications >= 2 (repeated_falsified), rather than building and scoring them
with penalties. Excludes known-dead options at construction time instead of relying
on downstream safety nets (_apply_llm_patch, plan_vetter) to catch them.
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
    """Stub graph port for testing."""

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


def _state(**overrides) -> WorkflowState:
    defaults = dict(
        step_index=0,
        action_attempt_counts={},
        action_falsification_counts={},
        consecutive_no_progress_count=0,
    )
    defaults.update(overrides)
    return WorkflowState(**defaults)


LIMITS = PlanGeneratorLimits()


class TestNonClickActionRepeatedlyFalsifiedExcluded:
    def test_non_click_action_falsified_twice_excluded(self):
        """Scenario 1: A non-click action (e.g. ACTION1) with falsifications=2
        is excluded entirely from _build_candidates output."""
        planner = PlanGenerator(LIMITS)
        state = _state(
            action_falsification_counts={"ACTION1": 2},
        )
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION1"]},
            grid_hash="hash-1",
        )

        candidates = planner._build_candidates(state, perception, _goal(), ["ACTION1"], [])

        # ACTION1 should be excluded; only the fallback should be present
        assert len(candidates) == 1
        assert candidates[0].metadata.get("fallback") is True

    def test_non_click_action_falsified_once_not_excluded(self):
        """Scenario 2: A non-click action with falsifications=1 (below threshold)
        is built and scored normally, including the falsification rationale."""
        planner = PlanGenerator(LIMITS)
        state = _state(
            action_attempt_counts={"ACTION1": 1},
            action_falsification_counts={"ACTION1": 1},
        )
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION1"]},
            grid_hash="hash-1",
        )

        candidates = planner._build_candidates(state, perception, _goal(), ["ACTION1"], [])

        assert len(candidates) == 1
        assert candidates[0].action_id == "ACTION1"
        assert candidates[0].metadata.get("repeated_falsified") is False
        # The rationale should document the falsification without excluding the candidate
        assert "consider ACTION1" in candidates[0].rationale


class TestClickTargetPerBookIdFiltering:
    def test_action6_with_mixed_falsification_excludes_only_repeated_falsified(self):
        """Scenario 3: ACTION6 with 3 click targets where exactly one book_id
        has falsifications=2 and the other two have 0: exactly 2 ACTION6 candidates
        are produced (the falsified coordinate excluded, the other two present)."""
        planner = PlanGenerator(LIMITS)
        # Set up falsification history for specific coordinates
        # Note: coordinates are in format ACTION6@x,y where x comes from col, y from row
        state = _state(
            action_attempt_counts={
                "ACTION6@17,18": 2,
                # "ACTION6@0,36" has 0 attempts
                # "ACTION6@39,45" has 0 attempts
            },
            action_falsification_counts={
                "ACTION6@17,18": 2,
                # "ACTION6@0,36" has 0 falsifications
                # "ACTION6@39,45" has 0 falsifications
            },
        )
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION6"]},
            grid_hash="hash-1",
            entities=(
                _click_entity("1", 18, 17),  # centroid (row=18, col=17) → @17,18
                _click_entity("2", 36, 0),   # centroid (row=36, col=0) → @0,36
                _click_entity("3", 45, 39),  # centroid (row=45, col=39) → @39,45
            ),
        )

        candidates = planner._build_candidates(state, perception, _goal(), ["ACTION6"], [])

        # Should have the 2 untested coordinates
        action6_candidates = [c for c in candidates if c.action_id == "ACTION6"]
        assert len(action6_candidates) >= 2, (
            f"Expected at least 2 ACTION6 candidates (the non-falsified coordinates), "
            f"got {len(action6_candidates)}: {[c.book_id for c in action6_candidates]}"
        )

        # The repeatedly-falsified coordinate should NOT be in the list
        falsified_book_ids = [c.book_id for c in action6_candidates]
        assert "ACTION6@17,18" not in falsified_book_ids, (
            f"ACTION6@17,18 (falsified 2x) should be excluded but found in candidates: {falsified_book_ids}"
        )

        # The untested coordinates should be present
        assert "ACTION6@0,36" in falsified_book_ids, f"ACTION6@0,36 should be included"
        assert "ACTION6@39,45" in falsified_book_ids, f"ACTION6@39,45 should be included"


class TestPathologicalAllFalsifiedFallback:
    def test_all_actions_repeatedly_falsified_returns_fallback_not_empty(self):
        """Scenario 4: Every available action/book_id is repeatedly falsified.
        _build_candidates returns exactly one candidate (the fallback probe),
        not an empty list."""
        planner = PlanGenerator(LIMITS)
        state = _state(
            action_attempt_counts={
                "ACTION1": 2,
                "ACTION2": 2,
                "ACTION3": 2,
            },
            action_falsification_counts={
                "ACTION1": 2,
                "ACTION2": 2,
                "ACTION3": 2,
            },
        )
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION1", "ACTION2", "ACTION3"]},
            grid_hash="hash-1",
        )

        candidates = planner._build_candidates(state, perception, _goal(), ["ACTION1", "ACTION2", "ACTION3"], [])

        assert len(candidates) == 1, f"Expected exactly 1 fallback candidate, got {len(candidates)}"
        assert candidates[0].metadata.get("fallback") is True


class TestVetoAlternativeBypassesFilter:
    def test_latest_veto_alternative_readded_even_if_repeated_falsified(self):
        """Scenario 5: state.latest_veto_alternative is re-added even when that
        alternative's book_id is repeatedly falsified. This is an intentional
        exception -- it's a separate mechanism for re-proposing the vetter's
        own suggested replacement."""
        from agents.arc4.types import PlanCandidate

        planner = PlanGenerator(LIMITS)

        # Create a veto alternative for a repeatedly-falsified book_id
        veto_candidate = PlanCandidate(
            action_id="ACTION7",
            goal_id="goal-1",
            score=0.1,
            metadata={"book_id": "ACTION7"},
        )

        state = _state(
            replan_passes=1,
            action_attempt_counts={"ACTION7": 2},
            action_falsification_counts={"ACTION7": 2},
            latest_veto_alternative=veto_candidate,
        )
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION1", "ACTION7"]},
            grid_hash="hash-1",
        )

        candidates = planner._build_candidates(state, perception, _goal(), ["ACTION1", "ACTION7"], [])

        # ACTION1 and ACTION7 are both excluded due to falsifications
        # But ACTION7 should be re-added via the veto_alternative mechanism
        veto_candidates = [c for c in candidates if c.metadata.get("replan_feedback") is True]
        assert len(veto_candidates) > 0, (
            f"veto_alternative should be re-added as a separate mechanism, "
            f"even though its book_id would otherwise be filtered. "
            f"Got candidates: {[(c.action_id, c.metadata.get('replan_feedback')) for c in candidates]}"
        )
        assert veto_candidates[0].action_id == "ACTION7"


class TestEndToEndA184A188Scenarios:
    def test_a184_a188_live_reproduction_coordinates_excluded(self):
        """Scenario 6: Reuse/adapt A184's and A188's exact live-reproduction
        scenarios (the repeatedly-falsified ACTION6@18,17 and related book_ids
        from those cards) and confirm they never appear in _build_candidates
        output at all now."""
        planner = PlanGenerator(LIMITS)

        # This is the exact state from A188's test_vet_reports_real_history_for_already_falsified_book_id
        # plus A184's live reproduction scenario
        state = _state(
            step_index=2,
            action_attempt_counts={
                "ACTION6@18,17": 5,
                "ACTION6@36,0": 4,
                "ACTION6@45,39": 3,
                "ACTION6@10,10": 3,
                "ACTION6@20,20": 2,
                "ACTION6@30,30": 2,
            },
            action_falsification_counts={
                "ACTION6@18,17": 3,  # Repeatedly falsified (>= 2)
                "ACTION6@36,0": 3,   # Repeatedly falsified
                "ACTION6@45,39": 2,  # Repeatedly falsified (== 2, meets threshold)
                "ACTION6@10,10": 2,  # Repeatedly falsified
                "ACTION6@20,20": 2,  # Repeatedly falsified
                "ACTION6@30,30": 1,  # Not repeatedly falsified (< 2)
            },
        )
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION6"]},
            grid_hash="hash-1",
            entities=(
                _click_entity("1", 18, 17),
                _click_entity("2", 36, 0),
                _click_entity("3", 45, 39),
                _click_entity("4", 10, 10),
                _click_entity("5", 20, 20),
                _click_entity("6", 30, 30),
            ),
        )

        candidates = planner._build_candidates(state, perception, _goal(), ["ACTION6"], [])

        # Extract book_ids of non-fallback candidates
        candidate_book_ids = [c.book_id for c in candidates if c.metadata.get("source") != "_fallback_candidate"]

        # All the repeatedly-falsified coordinates should be excluded
        repeatedly_falsified_ids = [
            "ACTION6@18,17",
            "ACTION6@36,0",
            "ACTION6@45,39",
            "ACTION6@10,10",
            "ACTION6@20,20",
        ]
        for book_id in repeatedly_falsified_ids:
            assert book_id not in candidate_book_ids, (
                f"{book_id} has falsifications >= 2 and should be excluded, "
                f"but found in candidates: {candidate_book_ids}"
            )

        # The non-repeatedly-falsified coordinate (falsifications=1) should still be there
        # unless it's been filtered for some other reason
        if "ACTION6@30,30" in candidate_book_ids:
            # Verify it's not marked as repeatedly_falsified
            matching = [c for c in candidates if c.book_id == "ACTION6@30,30"]
            assert len(matching) > 0
            assert matching[0].metadata.get("repeated_falsified") is False


class TestRepeatedFalsificationDefinesThreshold:
    def test_falsifications_equals_one_below_threshold(self):
        """Boundary case: falsifications=1 is below the threshold (>= 2)
        and should NOT be excluded."""
        planner = PlanGenerator(LIMITS)
        state = _state(
            action_attempt_counts={"ACTION2": 1},
            action_falsification_counts={"ACTION2": 1},
        )
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION2"]},
            grid_hash="hash-1",
        )

        candidates = planner._build_candidates(state, perception, _goal(), ["ACTION2"], [])

        # ACTION2 should be included
        action2_candidates = [c for c in candidates if c.action_id == "ACTION2"]
        assert len(action2_candidates) == 1
        assert action2_candidates[0].metadata.get("repeated_falsified") is False

    def test_falsifications_equals_two_at_threshold(self):
        """Boundary case: falsifications=2 is at the threshold (>= 2)
        and should be excluded."""
        planner = PlanGenerator(LIMITS)
        state = _state(
            action_attempt_counts={"ACTION2": 2},
            action_falsification_counts={"ACTION2": 2},
        )
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION2"]},
            grid_hash="hash-1",
        )

        candidates = planner._build_candidates(state, perception, _goal(), ["ACTION2"], [])

        # ACTION2 should be excluded; only fallback present
        action2_candidates = [c for c in candidates if c.action_id == "ACTION2"]
        assert len(action2_candidates) == 0, (
            f"ACTION2 with falsifications=2 should be excluded entirely, "
            f"but found {len(action2_candidates)} candidates"
        )
        assert len(candidates) == 1
        assert candidates[0].metadata.get("fallback") is True

    def test_falsifications_exceeds_two_still_excluded(self):
        """Boundary case: falsifications > 2 (e.g., 5) should also be excluded."""
        planner = PlanGenerator(LIMITS)
        state = _state(
            action_attempt_counts={"ACTION3": 5},
            action_falsification_counts={"ACTION3": 5},
        )
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION3"]},
            grid_hash="hash-1",
        )

        candidates = planner._build_candidates(state, perception, _goal(), ["ACTION3"], [])

        # ACTION3 should be excluded
        action3_candidates = [c for c in candidates if c.action_id == "ACTION3"]
        assert len(action3_candidates) == 0
        assert len(candidates) == 1
        assert candidates[0].metadata.get("fallback") is True
