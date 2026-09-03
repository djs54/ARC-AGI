"""A147: stall guard counts distinct base actions, not per-coordinate ACTION6 targets."""

from agents.arc4.cycle_policy import base_action, check_stall, count_base_actions, untested_remaining_actions


def test_base_action_strips_coordinates():
    assert base_action("ACTION6@10,20") == "ACTION6"
    assert base_action("ACTION1") == "ACTION1"
    assert base_action("ACTION6@0,0") == "ACTION6"


def test_count_base_actions_collapses_targets():
    keys = {"ACTION1", "ACTION6@1,1", "ACTION6@2,2", "ACTION6@3,3"}
    # ACTION1 + ACTION6 == 2 distinct base actions
    assert count_base_actions(keys) == 2


def test_count_base_actions_empty():
    assert count_base_actions([]) == 0
    assert count_base_actions({}) == 0


def test_check_stall_not_inflated_by_targets():
    # 5 base actions available; attempt keys hold 4 simple actions + 3 ACTION6 targets.
    # Collapsed that is 5 distinct base actions — all tried exactly once.
    available_actions = {"ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION6"}
    attempt_keys = {"ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION6@1,1", "ACTION6@2,2", "ACTION6@3,3"}
    num_attempted = count_base_actions(attempt_keys)
    assert num_attempted == 5
    # A248: check_stall's 4th arg is the caller's already-computed
    # "genuinely untested in the current action space" count (a set
    # difference), not the raw attempted count itself.
    untested = untested_remaining_actions(available_actions, attempt_keys)
    assert untested == 0
    # Below the two-pass threshold (5*2=10) → must not stall yet.
    assert check_stall(5, 4, 5, untested) is None


def test_check_stall_still_fires_after_two_passes():
    available_actions = {"ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION6"}
    attempt_keys = {"ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION6@1,1"}
    num_attempted = count_base_actions(attempt_keys)
    assert num_attempted == 5
    untested = untested_remaining_actions(available_actions, attempt_keys)
    assert untested == 0
    # Every base action tried, two full passes of no progress → stall.
    assert check_stall(10, 4, 5, untested) == "stall_detected"
