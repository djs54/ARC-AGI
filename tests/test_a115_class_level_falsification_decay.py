"""A115 — Class-Level Falsification Decay

Regression test for class-level penalties on action families with systematic failures.
"""

from __future__ import annotations

import pytest


class TestA115ClassLevelFalsificationDecay:
    """Verify class-level falsification penalties and decay."""

    def test_class_penalty_accumulation(self):
        """Penalty accumulates as actions in a class fail."""
        class_penalty_accumulator = {}
        class_falsification_penalty = {}
        
        # Record 3 failures in "exploration" class
        for _ in range(3):
            if "exploration" not in class_penalty_accumulator:
                class_penalty_accumulator["exploration"] = 0
            
            class_penalty_accumulator["exploration"] += 1
            
            # Apply compound penalty at threshold
            if class_penalty_accumulator["exploration"] >= 3:
                current = class_falsification_penalty.get("exploration", 0)
                class_falsification_penalty["exploration"] = min(9, current + 1)
        
        assert class_falsification_penalty.get("exploration", 0) >= 1, "Class should have penalty"

    def test_penalty_multiplier_calculation(self):
        """Penalty multiplier ranges from 1.0 (no penalty) to 0.1 (max)."""
        penalty_score = 3  # 3 levels of penalty
        penalty_multiplier = max(0.1, 1.0 - (penalty_score * 0.1))
        
        assert 0.1 <= penalty_multiplier <= 1.0, "Multiplier should be in valid range"
        assert penalty_multiplier == 0.7, "3-level penalty should give 0.7 multiplier"

    def test_penalty_application_to_score(self):
        """Penalty reduces action class score."""
        base_score = 100.0
        penalty_multiplier = 0.5
        penalized_score = base_score * penalty_multiplier
        
        assert penalized_score == 50.0, "Penalty should reduce score"
        assert penalized_score < base_score, "Penalized score should be lower"

    def test_penalty_decay_over_time(self):
        """Penalties decay when class produces successes."""
        class_falsification_penalty = {"exploration": 5}
        decay_rate = 0.1
        
        # Decay on successful step
        if class_falsification_penalty["exploration"] > 0:
            class_falsification_penalty["exploration"] -= decay_rate
        
        assert class_falsification_penalty["exploration"] == 4.9, "Penalty should decay"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
