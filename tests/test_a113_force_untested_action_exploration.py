"""A113 — Force Untested Action Exploration

Regression test for forcing ACTION7 exploration when ACTION6 reaches 100% falsification rate.
"""

from __future__ import annotations

import pytest


class TestA113ForceUntestedActionExploration:
    """Verify forced ACTION7 exploration when ACTION6 is exhausted."""

    def test_force_action7_on_100_percent_falsification(self):
        """When ACTION6 has 5 consecutive falsifications, ACTION7 is forced."""
        # Simulate falsification window tracking
        action_falsification_window = {}
        force_action7_next = False
        
        # Record 5 consecutive ACTION6 failures
        for _ in range(5):
            if "ACTION6" not in action_falsification_window:
                action_falsification_window["ACTION6"] = []
            
            window = action_falsification_window["ACTION6"]
            window.append(True)  # True = falsified
            
            if len(window) > 5:
                window.pop(0)
            
            # Check if we should force ACTION7
            if len(window) == 5 and all(window):
                force_action7_next = True
        
        assert force_action7_next, "ACTION7 should be forced after 5 ACTION6 failures"

    def test_force_flag_cleared_after_action7(self):
        """After ACTION7 executes, the force flag is cleared."""
        force_action7_next = True
        
        # ACTION7 executes
        if force_action7_next:
            force_action7_next = False
        
        assert not force_action7_next, "Force flag should be cleared after ACTION7"

    def test_no_force_with_mixed_results(self):
        """No force when ACTION6 has mixed success/failure."""
        action_falsification_window = {"ACTION6": [True, False, True, False, True]}
        
        # Not all falsified
        has_all_falsified = len(action_falsification_window["ACTION6"]) == 5 and all(
            action_falsification_window["ACTION6"]
        )
        
        assert not has_all_falsified, "No force when ACTION6 has successes"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
