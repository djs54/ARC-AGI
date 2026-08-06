"""A159: the STALL_CHECK diagnostic log must report the threshold check_stall
actually gates on, not just half of it.

Live evidence (game r11l-495a7899, 2026-08-05, single-action game): the log
showed "threshold=2" (num_available*2) at every step, but the run only
actually stalled once no_progress reached 4 (max_consecutive_no_progress) --
check_stall requires BOTH conditions, so the real effective threshold is
max(max_consecutive_no_progress, num_available*2), not just the latter.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.cycle_policy import check_stall, stall_threshold


def test_threshold_uses_floor_when_larger():
    # Single-action game: max_consecutive_no_progress=4 > num_available*2=2
    assert stall_threshold(max_consecutive_no_progress=4, num_available_actions=1) == 4


def test_threshold_uses_coverage_ceiling_when_larger():
    # Rich action space: num_available*2=12 > max_consecutive_no_progress=4
    assert stall_threshold(max_consecutive_no_progress=4, num_available_actions=6) == 12


def test_threshold_handles_zero_available_actions():
    assert stall_threshold(max_consecutive_no_progress=4, num_available_actions=0) == 4


def test_check_stall_still_matches_original_behavior_at_floor():
    # Matches the live evidence: no_progress=2 with a single-action game must
    # NOT stall yet (the run correctly continued past this point).
    result = check_stall(
        consecutive_no_progress=2,
        max_consecutive_no_progress=4,
        num_available_actions=1,
        num_attempted_actions=1,
    )
    assert result is None


def test_check_stall_fires_at_floor():
    result = check_stall(
        consecutive_no_progress=4,
        max_consecutive_no_progress=4,
        num_available_actions=1,
        num_attempted_actions=1,
    )
    assert result == "stall_detected"
