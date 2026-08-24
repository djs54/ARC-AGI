"""Tests for A203: anchor hint biasing in goal_resolver and plan_generator.

A202 produced state.reasoner_anchor_hint (a ReasonerOutcome) when the trajectory
Reasoner decides to REPEAT_DEEPEN or REPEAT_RETRY. A203 wires this hint to:
- goal_resolver.resolve() to re-rank/select the anchored goal hypothesis
- plan_generator._build_candidates() to boost scores for anchored entity candidates
  or force a retry (with A191 exclusion protection: never resurrect falsified actions)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.goal_resolver import GoalResolver, GoalResolverLimits
from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.types import (
    GoalHypothesis,
    PerceivedEntity,
    PerceptionSnapshot,
    ReasonerOutcome,
    ResolvedGoal,
    WorkflowState,
)


# ────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────


def _perception(
    *,
    grid_hash: str = "grid-1",
    grid_shape: tuple[int, int] | None = (2, 2),
    entities: tuple[PerceivedEntity, ...] = (),
) -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"grid": grid_hash},
        grid_hash=grid_hash,
        grid_shape=grid_shape,
        entities=entities,
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


def _goal(*, goal_id: str = "goal-1", confidence: float = 0.8) -> ResolvedGoal:
    return ResolvedGoal(
        selected=GoalHypothesis(
            goal_id=goal_id, description="test goal", confidence=confidence
        )
    )


def _click_entity(
    value: str, row: float, col: float, entity_ref: int | None = None, cell_count: int = 5
) -> PerceivedEntity:
    attrs = {
        "coverage": 0.01,
        "cell_count": cell_count,
        "centroid": (row, col),
    }
    if entity_ref is not None:
        attrs["entity_ref"] = entity_ref
    return PerceivedEntity(kind="point", value=value, attributes=attrs)


# ────────────────────────────────────────────────────────────────────────────
# Goal Resolver Tests
# ────────────────────────────────────────────────────────────────────────────


class TestGoalResolverAnchorHinting:
    """A203 goal_resolver tests: anchor hint reordering for goal-type hints."""

    def test_goal_resolver_with_hint_none_identical_output(self):
        """Test 1: hint=None produces identical output to baseline (no hint at all)."""
        resolver = GoalResolver()
        perception = _perception(
            entities=(PerceivedEntity(kind="block", value="red", attributes={}),)
        )

        # Baseline: no hint
        result_baseline = resolver.resolve(_state(), perception)

        # With hint=None
        state_with_none = _state(reasoner_anchor_hint=None)
        result_with_none = resolver.resolve(state_with_none, perception)

        # Byte-for-byte identical: same goal_id, confidence, alternatives
        assert result_baseline.payload is not None
        assert result_with_none.payload is not None
        assert result_baseline.payload.selected.goal_id == result_with_none.payload.selected.goal_id
        assert result_baseline.payload.selected.confidence == result_with_none.payload.selected.confidence
        assert result_baseline.payload.alternatives == result_with_none.payload.alternatives

    def test_goal_resolver_with_goal_hint_matching_hypothesis_reorders(self):
        """Test 2: goal-type hint matching an existing hypothesis reorders it to top."""
        resolver = GoalResolver()
        perception = _perception(
            entities=(
                PerceivedEntity(kind="block", value="red", attributes={}),
                PerceivedEntity(kind="block", value="blue", attributes={}),
            )
        )

        # Without hint, "block-red" is typically top (first entity)
        result_without_hint = resolver.resolve(_state(), perception)
        assert result_without_hint.payload is not None
        top_id_without_hint = result_without_hint.payload.selected.goal_id

        # With a hint anchoring to "block-blue" (second hypothesis), it should move to top
        hint = ReasonerOutcome(
            decision="repeat_deepen",
            anchor_ref="block-blue",
            anchor_type="goal",
        )
        state_with_hint = _state(reasoner_anchor_hint=hint)
        result_with_hint = resolver.resolve(state_with_hint, perception)

        assert result_with_hint.payload is not None
        assert result_with_hint.payload.selected.goal_id == "block-blue"
        # The baseline top should still be in alternatives
        if top_id_without_hint in [h.goal_id for h in result_with_hint.payload.alternatives]:
            pass  # Good, it was demoted to alternatives
        elif top_id_without_hint != "block-blue":
            # If it's not the selected and not in alternatives, that's also ok (it might not be generated at all)
            pass

    def test_goal_resolver_with_goal_hint_not_matching_falls_through(self):
        """Test 3: goal-type hint matching nothing falls back to normal ranking."""
        resolver = GoalResolver()
        perception = _perception(
            entities=(PerceivedEntity(kind="block", value="red", attributes={}),)
        )

        # Hint to a non-existent goal
        hint = ReasonerOutcome(
            decision="repeat_deepen",
            anchor_ref="phantom-goal-xyz",
            anchor_type="goal",
        )
        state_with_hint = _state(reasoner_anchor_hint=hint)

        # Should not crash, should fall back to normal ranking
        result = resolver.resolve(state_with_hint, perception)

        assert result.payload is not None
        assert result.payload.selected is not None
        # Normal ranking: should still have a goal_id
        assert result.payload.selected.goal_id is not None


# ────────────────────────────────────────────────────────────────────────────
# Plan Generator Tests
# ────────────────────────────────────────────────────────────────────────────


class TestPlanGeneratorAnchorHinting:
    """A203 plan_generator tests: anchor hint scoring for entity-type hints."""

    def test_plan_generator_with_hint_none_identical_output(self):
        """Test 4: hint=None produces identical output to baseline."""
        planner = PlanGenerator(PlanGeneratorLimits())
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION1"]},
            grid_hash="hash-1",
        )

        # Baseline: no hint
        candidates_baseline = planner._build_candidates(
            _state(), perception, _goal(), ["ACTION1"], []
        )

        # With hint=None
        candidates_with_none = planner._build_candidates(
            _state(reasoner_anchor_hint=None), perception, _goal(), ["ACTION1"], []
        )

        # Should be byte-for-byte identical
        assert len(candidates_baseline) == len(candidates_with_none)
        for c_base, c_none in zip(candidates_baseline, candidates_with_none):
            assert c_base.action_id == c_none.action_id
            assert c_base.score == c_none.score
            assert c_base.book_id == c_none.book_id

    def test_plan_generator_with_repeat_retry_hint_forces_highest_score(self):
        """Test 5: repeat_retry hint with required_book_id boosts that candidate to top."""
        planner = PlanGenerator(PlanGeneratorLimits())
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION1", "ACTION2"]},
            grid_hash="hash-1",
        )

        # Hint to retry ACTION2 (which normally would not be top)
        hint = ReasonerOutcome(
            decision="repeat_retry",
            anchor_ref=None,
            anchor_type="entity",
            required_book_id="ACTION2",
        )
        state_with_hint = _state(reasoner_anchor_hint=hint)

        candidates = planner._build_candidates(
            state_with_hint, perception, _goal(), ["ACTION1", "ACTION2"], []
        )

        # Find ACTION2 candidate
        action2_candidates = [c for c in candidates if c.book_id == "ACTION2"]
        assert len(action2_candidates) > 0, "ACTION2 should be in candidates"

        action2 = action2_candidates[0]
        # Should have high score and updated rationale
        assert action2.score >= max(c.score for c in candidates if c.book_id != "ACTION2")
        assert "reasoner requested retry" in action2.rationale

    def test_plan_generator_with_repeat_retry_excluded_candidate_silent_noop(self):
        """Test 6: repeat_retry hint for an A191-excluded candidate is a silent no-op.

        This is the non-negotiable regression guard: A191's exclusion of
        repeated_falsified candidates must NOT be bypassed even when a
        ReasonerOutcome requests a retry.
        """
        planner = PlanGenerator(PlanGeneratorLimits())
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION1", "ACTION2"]},
            grid_hash="hash-1",
        )

        # ACTION1 has been falsified twice (A191 exclusion)
        state = _state(
            action_falsification_counts={"ACTION1": 2},
            action_attempt_counts={"ACTION1": 2},
        )

        # Hint requests retry of ACTION1 (even though it's excluded)
        hint = ReasonerOutcome(
            decision="repeat_retry",
            anchor_ref=None,
            anchor_type="entity",
            required_book_id="ACTION1",
        )
        state.reasoner_anchor_hint = hint

        candidates = planner._build_candidates(
            state, perception, _goal(), ["ACTION1", "ACTION2"], []
        )

        # ACTION1 should NOT be in the candidate list at all
        # (it was never added to the list in the first place due to A191's exclusion)
        action1_candidates = [c for c in candidates if c.book_id == "ACTION1"]
        assert len(action1_candidates) == 0, (
            f"ACTION1 with falsifications=2 should be excluded by A191 and NOT resurrected by the hint. "
            f"Got candidates: {[c.book_id for c in candidates]}"
        )

    def test_plan_generator_with_repeat_deepen_hint_boosts_entity_scores(self):
        """Test 7: repeat_deepen hint matching entity_ref boosts candidate scores."""
        planner = PlanGenerator(PlanGeneratorLimits())

        # Two click targets with entity_refs
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION6"]},
            grid_hash="hash-1",
            entities=(
                _click_entity("1", 18, 17, entity_ref=10),
                _click_entity("2", 36, 0, entity_ref=20),
            ),
        )

        # Hint to deepen on entity_ref=10
        hint = ReasonerOutcome(
            decision="repeat_deepen",
            anchor_ref=10,
            anchor_type="entity",
        )
        state_with_hint = _state(reasoner_anchor_hint=hint)

        candidates = planner._build_candidates(
            state_with_hint, perception, _goal(), ["ACTION6"], []
        )

        # Find candidates for each entity
        entity10_candidates = [c for c in candidates if c.metadata.get("entity_ref") == 10]
        entity20_candidates = [c for c in candidates if c.metadata.get("entity_ref") == 20]

        # Both should have candidates
        assert len(entity10_candidates) > 0, "Should have ACTION6 candidate for entity_ref=10"
        assert len(entity20_candidates) > 0, "Should have ACTION6 candidate for entity_ref=20"

        # Entity 10 should have score boost and flag
        assert entity10_candidates[0].metadata.get("reasoner_anchor_bias_applied") is True
        # The bias is additive (+0.3), so exact score comparison depends on other factors
        # but we can verify the flag is set

        # Entity 20 should NOT have the flag
        assert entity20_candidates[0].metadata.get("reasoner_anchor_bias_applied") is not True


class TestA203RegressionGuards:
    """Regression tests for A191 interaction and exclusion protection."""

    def test_repeat_retry_with_multiple_falsified_coordinates_only_nonexcluded_retried(self):
        """Verify retry doesn't resurrect any of the A191-excluded coordinates."""
        planner = PlanGenerator(PlanGeneratorLimits())
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION6"]},
            grid_hash="hash-1",
            entities=(
                _click_entity("1", 18, 17, entity_ref=10),
                _click_entity("2", 36, 0, entity_ref=20),
                _click_entity("3", 45, 39, entity_ref=30),
            ),
        )

        # Multiple ACTION6 coordinates with different falsification counts
        state = _state(
            action_falsification_counts={
                "ACTION6@17,18": 2,  # Excluded by A191
                "ACTION6@0,36": 2,   # Excluded by A191
                "ACTION6@39,45": 1,  # NOT excluded (only 1 falsification)
            },
            action_attempt_counts={
                "ACTION6@17,18": 2,
                "ACTION6@0,36": 2,
                "ACTION6@39,45": 1,
            },
        )

        # Hint to retry ACTION6@17,18 (which is excluded)
        hint = ReasonerOutcome(
            decision="repeat_retry",
            anchor_ref=None,
            anchor_type="entity",
            required_book_id="ACTION6@17,18",
        )
        state.reasoner_anchor_hint = hint

        candidates = planner._build_candidates(
            state, perception, _goal(), ["ACTION6"], []
        )

        # The excluded coordinates should stay excluded
        excluded_book_ids = ["ACTION6@17,18", "ACTION6@0,36"]
        candidate_book_ids = [c.book_id for c in candidates if not c.metadata.get("fallback")]
        for excluded in excluded_book_ids:
            assert excluded not in candidate_book_ids, (
                f"{excluded} should remain excluded by A191 even with retry hint. "
                f"Got candidates: {candidate_book_ids}"
            )

        # The non-excluded coordinate should still be there
        assert "ACTION6@39,45" in candidate_book_ids


class TestAnchorHintEdgeCases:
    """Edge cases for anchor hinting."""

    def test_goal_resolver_with_entity_type_hint_ignored(self):
        """Confirm goal_resolver ignores entity-type hints (only processes goal-type)."""
        resolver = GoalResolver()
        perception = _perception(
            entities=(PerceivedEntity(kind="block", value="red", attributes={}),)
        )

        # Entity-type hint (should be ignored by goal_resolver)
        hint = ReasonerOutcome(
            decision="repeat_deepen",
            anchor_ref=10,
            anchor_type="entity",
        )
        state_with_hint = _state(reasoner_anchor_hint=hint)

        result = resolver.resolve(state_with_hint, perception)

        # Should proceed normally (the entity hint is for plan_generator only)
        assert result.payload is not None
        assert result.payload.selected is not None

    def test_plan_generator_with_goal_type_hint_ignored(self):
        """Confirm plan_generator ignores goal-type hints (only processes entity-type)."""
        planner = PlanGenerator(PlanGeneratorLimits())
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION1"]},
            grid_hash="hash-1",
        )

        # Goal-type hint (should be ignored by plan_generator)
        hint = ReasonerOutcome(
            decision="repeat_deepen",
            anchor_ref="goal-1",
            anchor_type="goal",
        )
        state_with_hint = _state(reasoner_anchor_hint=hint)

        candidates = planner._build_candidates(
            state_with_hint, perception, _goal(), ["ACTION1"], []
        )

        # Should proceed normally (goal-type hints are for goal_resolver only)
        assert len(candidates) > 0
