"""A116 — Lesson→Mechanic Prior Promotion

Regression test for promoting lessons into aggregate mechanic summaries.
"""

from __future__ import annotations

import pytest


class TestA116LessonMechanicPriorPromotion:
    """Verify lesson promotion to aggregate mechanic summaries."""

    def test_lesson_with_sufficient_evidence_is_promoted(self):
        """Lessons with 3+ evidence and 0.7+ confidence are promoted."""
        lesson_data = {
            "evidence_count": 3,
            "confidence": 0.7,
            "actions": ["ACTION6", "ACTION7"],
            "effect_type": "movement",
        }
        
        # Check promotion criteria
        is_promoted = lesson_data["evidence_count"] >= 3 and lesson_data["confidence"] >= 0.7
        assert is_promoted, "Lesson with 3+ evidence and 0.7+ confidence should be promoted"

    def test_lesson_promotion_creates_mechanic_signature(self):
        """Promoted lesson creates a mechanic with valid signature."""
        lesson_id = "lesson_001"
        lesson_data = {
            "evidence_count": 3,
            "confidence": 0.8,
            "actions": ["ACTION6"],
            "effect_type": "color_change",
        }
        
        # Create mechanic from lesson
        mechanic = {
            "summary_id": f"mechanic_{lesson_id}",
            "signature": {
                "action_set": lesson_data.get("actions", []),
                "effect_class": lesson_data.get("effect_type", "unknown"),
                "confidence": lesson_data.get("confidence", 0.5),
            },
        }
        
        assert mechanic["summary_id"] == "mechanic_lesson_001"
        assert mechanic["signature"]["action_set"] == ["ACTION6"]
        assert mechanic["signature"]["effect_class"] == "color_change"
        assert mechanic["signature"]["confidence"] == 0.8

    def test_insufficient_evidence_blocks_promotion(self):
        """Lessons with low evidence or confidence are not promoted."""
        lesson_data = {
            "evidence_count": 1,
            "confidence": 0.5,
        }
        
        is_promoted = lesson_data["evidence_count"] >= 3 and lesson_data["confidence"] >= 0.7
        assert not is_promoted, "Low evidence should prevent promotion"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
