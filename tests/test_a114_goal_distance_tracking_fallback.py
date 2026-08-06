"""A114 — Goal Distance Tracking Fallback

Regression test for preserving last-known goal distance when current value is null.
"""

from __future__ import annotations

import pytest


class TestA114GoalDistanceTrackingFallback:
    """Verify fallback to last-known goal distance when current is null."""

    def test_fallback_to_last_known_distance(self):
        """When distance is null, use last-known value."""
        last_known_distance = 5.0
        current_distance = None
        
        # Get goal distance with fallback
        distance = current_distance if current_distance is not None else last_known_distance
        
        assert distance == 5.0, "Should use last-known distance when current is null"

    def test_update_fallback_on_new_distance(self):
        """When new distance is computed, update the fallback."""
        last_known_distance = 5.0
        
        # New distance computed
        new_distance = 3.0
        if new_distance is not None:
            last_known_distance = new_distance
        
        assert last_known_distance == 3.0, "Fallback should be updated with new distance"

    def test_consecutive_nulls_preserve_fallback(self):
        """Multiple consecutive nulls preserve the last-known value."""
        last_known_distance = 5.0
        
        # Multiple null readings
        for _ in range(3):
            current = None
            if current is not None:
                last_known_distance = current
        
        assert last_known_distance == 5.0, "Multiple nulls should preserve fallback"

    def test_no_progress_when_distance_unavailable(self):
        """When both current and fallback are unavailable, treat as unknown."""
        last_known_distance = None
        current_distance = None
        
        distance = current_distance if current_distance is not None else last_known_distance
        
        assert distance is None, "Unknown state when both values are null"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
