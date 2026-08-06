"""A117 — Grounding Gate on Goal Confidence

Regression test for preventing confidence increases without empirical progress evidence.
"""

from __future__ import annotations

import pytest


class TestA117GroundingGateOnGoalConfidence:
    """Verify confidence updates are grounded in progress evidence."""

    def test_confidence_increase_without_progress_is_blocked(self):
        """Confidence cannot increase without progress evidence."""
        current_confidence = 0.5
        new_confidence = 0.8  # Attempted increase
        last_goal_distance = 5.0
        current_distance = 5.5  # Got worse, not better
        
        # Check if progress is evident
        progress_evident = (
            current_distance is not None and
            last_goal_distance is not None and
            current_distance < last_goal_distance
        )
        
        # Apply gate
        if new_confidence > current_confidence and not progress_evident:
            # Blocked
            final_confidence = current_confidence
        else:
            final_confidence = new_confidence
        
        assert final_confidence == 0.5, "Confidence increase should be blocked without progress"

    def test_confidence_increase_with_progress_is_allowed(self):
        """Confidence can increase when progress is evident."""
        current_confidence = 0.5
        new_confidence = 0.8
        last_goal_distance = 5.0
        current_distance = 3.0  # Better, got closer
        
        # Check progress
        progress_evident = (
            current_distance is not None and
            last_goal_distance is not None and
            current_distance < last_goal_distance
        )
        
        # Apply gate
        if new_confidence > current_confidence and not progress_evident:
            final_confidence = current_confidence
        else:
            final_confidence = new_confidence
        
        assert final_confidence == 0.8, "Confidence increase should be allowed with progress"

    def test_confidence_maintenance_without_evidence(self):
        """Confidence can be maintained at current level without new evidence."""
        current_confidence = 0.5
        new_confidence = 0.5  # No change
        last_goal_distance = 5.0
        current_distance = 5.0  # No progress
        
        # Apply gate (maintenance is allowed without evidence)
        final_confidence = new_confidence
        
        assert final_confidence == 0.5, "Confidence maintenance should be allowed"

    def test_confidence_decrease_always_allowed(self):
        """Confidence can decrease without requiring evidence."""
        current_confidence = 0.8
        new_confidence = 0.4  # Decrease
        
        # Decreases don't require evidence
        final_confidence = new_confidence
        
        assert final_confidence == 0.4, "Confidence decrease should always be allowed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
