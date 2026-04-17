from __future__ import annotations

from agents.arc3.solver import ArchetypeClassifier, GameArchetype


def test_fast_track_classify_space_signal():
    clf = ArchetypeClassifier()
    archetype, confidence = clf.fast_track_classify(
        {
            "n_regions": 4,
            "region_sizes": [120, 12, 5, 3],
            "colors": [0, 1, 2, 3],
        }
    )

    assert archetype == GameArchetype.SPACE
    assert confidence >= 0.3


def test_classifier_produces_guidance_by_second_observation():
    clf = ArchetypeClassifier()
    context = {
        "action_facts": [
            {"action": "ACTION1", "fact_type": "deterministic_effect", "value_status": "valuable", "trend": {"direction": "up"}},
            {"action": "ACTION2", "fact_type": "deterministic_effect", "value_status": "valuable", "trend": {"direction": "down"}},
        ],
        "hud_rows": [],
        "path_hypotheses": [{"value_status": "tentative"}, {"value_status": "tentative"}],
        "last_transition_effect": {"pixels_changed": 14},
        "bootstrap_grid_analysis": {"n_regions": 4, "region_sizes": [120, 7, 5, 2]},
    }

    first = clf.update(context)
    second = clf.update(context)

    assert first[0] in {GameArchetype.UNKNOWN, GameArchetype.SPACE}
    assert second[0] != GameArchetype.UNKNOWN
    assert second[1] > 0.0
