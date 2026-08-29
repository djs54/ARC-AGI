"""Tests for A219: entity-level effect-type computation from A175's existing
frame-to-frame entity correspondence, plus disappearance detection (not
computed anywhere before this card). Telemetry-only in this card -- no
scoring/graph wiring. See backlog/A219.md and
backlog/plans/A-219-entity-effect-type-perception-layer.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.perceive import PerceiveAgent
from agents.arc4.rule_extraction import EffectType, classify_effect_type
from agents.arc4.types import PerceivedEntity, WorkflowState
from agents.arc4.telemetry import ArcV2Telemetry


# ── classify_effect_type() unit tests ──────────────────────────────────────


class TestClassifyEffectTypeAppearanceDisappearance:
    def test_no_predecessor_is_appearance(self):
        current = {"centroid": (1.0, 1.0), "cell_count": 3}
        assert classify_effect_type(None, current) == EffectType.APPEARANCE

    def test_unclaimed_previous_is_disappearance(self):
        previous = {"centroid": (1.0, 1.0), "cell_count": 3}
        assert classify_effect_type(previous, None) == EffectType.DISAPPEARANCE

    def test_both_none_is_unchanged_not_a_crash(self):
        """Degenerate input, not expected from real call sites, but must
        degrade gracefully rather than raise."""
        assert classify_effect_type(None, None) == EffectType.UNCHANGED


class TestClassifyEffectTypeTranslation:
    def test_centroid_move_beyond_threshold_is_translation(self):
        previous = {"centroid": (0.0, 0.0), "cell_count": 4}
        current = {"centroid": (0.0, 5.0), "cell_count": 4}
        assert classify_effect_type(previous, current) == EffectType.TRANSLATION

    def test_tiny_centroid_jitter_below_threshold_is_unchanged(self):
        """Noise tolerance matters -- a real grid has pixel-level jitter that
        isn't a real move, per the plan's explicit edge case."""
        previous = {"centroid": (0.0, 0.0), "cell_count": 4}
        current = {"centroid": (0.0, 0.5), "cell_count": 4}
        assert classify_effect_type(previous, current) == EffectType.UNCHANGED

    def test_move_exactly_at_threshold_is_not_translation(self):
        previous = {"centroid": (0.0, 0.0), "cell_count": 4}
        current = {"centroid": (0.0, 2.0), "cell_count": 4}
        result = classify_effect_type(previous, current, translation_threshold=2.0)
        assert result == EffectType.UNCHANGED

    def test_custom_translation_threshold_respected(self):
        previous = {"centroid": (0.0, 0.0), "cell_count": 4}
        current = {"centroid": (0.0, 1.0), "cell_count": 4}
        assert classify_effect_type(previous, current, translation_threshold=0.5) == EffectType.TRANSLATION
        assert classify_effect_type(previous, current, translation_threshold=5.0) == EffectType.UNCHANGED


class TestClassifyEffectTypeGrowthShrink:
    def test_cell_count_increase_beyond_threshold_is_growth(self):
        previous = {"centroid": (2.0, 2.0), "cell_count": 4}
        current = {"centroid": (2.0, 2.0), "cell_count": 10}
        assert classify_effect_type(previous, current) == EffectType.GROWTH

    def test_cell_count_decrease_beyond_threshold_is_shrink(self):
        previous = {"centroid": (2.0, 2.0), "cell_count": 10}
        current = {"centroid": (2.0, 2.0), "cell_count": 4}
        assert classify_effect_type(previous, current) == EffectType.SHRINK

    def test_cell_count_delta_within_threshold_is_unchanged(self):
        previous = {"centroid": (2.0, 2.0), "cell_count": 4}
        current = {"centroid": (2.0, 2.0), "cell_count": 5}
        assert classify_effect_type(previous, current, size_delta_threshold=1) == EffectType.UNCHANGED

    def test_custom_size_delta_threshold_respected(self):
        previous = {"centroid": (2.0, 2.0), "cell_count": 4}
        current = {"centroid": (2.0, 2.0), "cell_count": 6}
        assert classify_effect_type(previous, current, size_delta_threshold=1) == EffectType.GROWTH
        assert classify_effect_type(previous, current, size_delta_threshold=5) == EffectType.UNCHANGED


class TestClassifyEffectTypeUnchangedAndPriority:
    def test_identical_attributes_is_unchanged(self):
        previous = {"centroid": (3.0, 3.0), "cell_count": 6}
        current = {"centroid": (3.0, 3.0), "cell_count": 6}
        assert classify_effect_type(previous, current) == EffectType.UNCHANGED

    def test_missing_attribute_keys_do_not_crash(self):
        """Degrades gracefully if centroid or cell_count is absent from
        either side."""
        assert classify_effect_type({}, {}) == EffectType.UNCHANGED
        assert classify_effect_type({"cell_count": 4}, {"cell_count": 4}) == EffectType.UNCHANGED
        assert classify_effect_type({"centroid": (0.0, 0.0)}, {"centroid": (0.0, 0.0)}) == EffectType.UNCHANGED

    def test_translation_takes_priority_over_growth_when_both_present(self):
        """Documented, arbitrary tie-break per classify_effect_type's own
        docstring: an entity that both moved and resized in the same step
        classifies as TRANSLATION, not GROWTH/SHRINK."""
        previous = {"centroid": (0.0, 0.0), "cell_count": 4}
        current = {"centroid": (0.0, 10.0), "cell_count": 10}
        assert classify_effect_type(previous, current) == EffectType.TRANSLATION


# ── Disappearance detection ─────────────────────────────────────────────────


def _entity(entity_ref: int, centroid: tuple[float, float], cell_count: int = 1, value: str = "5") -> PerceivedEntity:
    return PerceivedEntity(
        kind="point",
        value=value,
        attributes={"entity_ref": entity_ref, "centroid": centroid, "cell_count": cell_count},
    )


class TestFindDisappearedEntities:
    def test_unclaimed_previous_entity_is_reported_disappeared(self):
        previous = (_entity(0, (0.0, 0.0)), _entity(1, (5.0, 5.0)))
        current = (_entity(0, (0.0, 1.0)),)  # entity_ref 1 not present this frame

        disappeared = PerceiveAgent._find_disappeared_entities(previous, current)

        assert len(disappeared) == 1
        assert disappeared[0].attributes["entity_ref"] == 1

    def test_all_claimed_means_nothing_disappeared(self):
        previous = (_entity(0, (0.0, 0.0)),)
        current = (_entity(0, (0.0, 1.0)),)

        disappeared = PerceiveAgent._find_disappeared_entities(previous, current)

        assert disappeared == ()

    def test_no_previous_entities_means_nothing_disappeared(self):
        current = (_entity(0, (0.0, 0.0)),)
        assert PerceiveAgent._find_disappeared_entities((), current) == ()

    def test_end_to_end_disappearance_via_perceive(self):
        """A real entity present in frame 1 and genuinely absent (not moved)
        in frame 2 must show up in the new disappeared-entities output --
        this was not possible before this card (A216 Part 2's gap)."""
        agent = PerceiveAgent()
        state = WorkflowState()

        first = agent.perceive(state, {"grid": [[5, 0, 0, 0, 0, 0, 0, 0, 0, 0]]})
        first_ref = first.payload.entities[0].attributes["entity_ref"]

        second = agent.perceive(state, {"grid": [[0, 0, 0, 0, 0, 0, 0, 0, 0, 0]]})

        entity_effects = second.payload.metadata["entity_effects"]
        disappeared_refs = [
            effect["entity_ref"] for effect in entity_effects if effect["effect_type"] == EffectType.DISAPPEARANCE.value
        ]
        assert first_ref in disappeared_refs


# ── Telemetry wiring (read-only, additive) ──────────────────────────────────


class TestEntityEffectsInTelemetry:
    def test_entity_effects_present_in_step_snapshot(self):
        agent = PerceiveAgent()
        state = WorkflowState()

        agent.perceive(state, {"grid": [[0, 0, 0, 0, 0], [0, 5, 0, 0, 0], [0, 0, 0, 0, 0]]})
        second = agent.perceive(state, {"grid": [[0, 0, 0, 0, 0], [0, 0, 0, 0, 5], [0, 0, 0, 0, 0]]})
        perception = second.payload

        telemetry = ArcV2Telemetry(task_id="test_task", game_id="test_game", append_snapshot=None)
        snapshot = telemetry._step_snapshot((state, perception))

        assert "entity_effects" in snapshot
        assert snapshot["entity_effects"] == perception.metadata["entity_effects"]
        assert len(snapshot["entity_effects"]) == 1
        assert snapshot["entity_effects"][0]["effect_type"] == EffectType.TRANSLATION.value

    def test_entity_effects_defaults_to_empty_list_without_perception(self):
        telemetry = ArcV2Telemetry(task_id="test_task", game_id="test_game", append_snapshot=None)
        state = WorkflowState()

        snapshot = telemetry._step_snapshot((state,))

        assert snapshot.get("entity_effects", []) == []

    def test_appearance_and_unchanged_visible_in_real_perceive_metadata(self):
        agent = PerceiveAgent()
        state = WorkflowState()

        first = agent.perceive(state, {"grid": [[0, 0], [0, 0]]})
        assert first.payload.metadata["entity_effects"] == []

        second = agent.perceive(state, {"grid": [[5, 0], [0, 0]]})
        effects = second.payload.metadata["entity_effects"]
        assert len(effects) == 1
        assert effects[0]["effect_type"] == EffectType.APPEARANCE.value

        third = agent.perceive(state, {"grid": [[5, 0], [0, 0]]})
        effects_unchanged = third.payload.metadata["entity_effects"]
        assert len(effects_unchanged) == 1
        assert effects_unchanged[0]["effect_type"] == EffectType.UNCHANGED.value


# ── Regression: existing correspondence behavior byte-for-byte unchanged ───


class TestExistingCorrespondenceBehaviorUnchanged:
    """A219 must not relax `_assign_correspondence`'s color-locked matching.
    These mirror test_a175_entity_identity_correspondence.py's own
    assertions to confirm the new disappearance/effect-type computation
    added alongside it doesn't alter existing entity_ref assignment."""

    def test_same_entity_matched_across_steps(self):
        agent = PerceiveAgent()
        state = WorkflowState()

        first = agent.perceive(state, {"grid": [[0, 0, 0], [0, 5, 0], [0, 0, 0]]})
        second = agent.perceive(state, {"grid": [[0, 0, 0], [0, 0, 5], [0, 0, 0]]})

        assert first.payload.entities[0].attributes["entity_ref"] == second.payload.entities[0].attributes["entity_ref"]

    def test_color_locked_matching_still_prevents_recolor_correspondence(self):
        """A recolored-in-place entity must still look like disappear+appear,
        not a tracked recolor -- RECOLOR is explicitly out of scope for this
        card (see backlog/A219.md Scope note)."""
        agent = PerceiveAgent()
        state = WorkflowState()

        first = agent.perceive(state, {"grid": [[5]]})
        first_ref = first.payload.entities[0].attributes["entity_ref"]

        second = agent.perceive(state, {"grid": [[6]]})
        second_ref = second.payload.entities[0].attributes["entity_ref"]

        assert second_ref != first_ref, "color-locked matching must remain unchanged by this card"

        effects = second.payload.metadata["entity_effects"]
        effect_types = {effect["effect_type"] for effect in effects}
        assert effect_types == {EffectType.APPEARANCE.value, EffectType.DISAPPEARANCE.value}
