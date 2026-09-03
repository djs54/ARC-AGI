from __future__ import annotations

from agents.arc4.cycle_policy import check_budget, check_stall, record_evaluation_outcome, termination_from_evaluation


def test_check_budget_boundary():
    assert check_budget(9, 10) is None
    assert check_budget(10, 10) == "budget_exhausted"


def test_check_stall_below_threshold():
    # A248: check_stall's 4th arg is the caller's already-computed
    # "genuinely untested in the current action space" count, not a raw
    # attempted count. 0 untested remaining == every available action tried.
    assert check_stall(3, 4, 5, 0) is None


def test_check_stall_untested_remaining():
    # 2 of the 5 available actions genuinely untested -> early pass.
    assert check_stall(6, 4, 5, 2) is None


def test_check_stall_two_full_passes():
    assert check_stall(9, 4, 5, 0) is None
    assert check_stall(10, 4, 5, 0) == "stall_detected"


def test_check_stall_empty_action_list():
    assert check_stall(1, 1, 0, 0) is None
    assert check_stall(2, 1, 0, 0) == "stall_detected"


def test_record_outcome_progress_resets():
    falsifications = {"ACTION1": 3}
    no_progress = record_evaluation_outcome(
        no_progress_count=4,
        falsification_counts=falsifications,
        action_key="ACTION1",
        meaningful_progress=True,
        falsification_delta=5,
    )

    assert no_progress == 0
    assert falsifications["ACTION1"] == 3


def test_record_outcome_falsification_floor():
    falsifications: dict[str, int] = {}
    no_progress = record_evaluation_outcome(
        no_progress_count=0,
        falsification_counts=falsifications,
        action_key="ACTION1",
        meaningful_progress=False,
        falsification_delta=0,
    )

    assert no_progress == 1
    assert falsifications["ACTION1"] == 1


def test_termination_mapping():
    assert termination_from_evaluation("terminate", "done") == ("terminated", "done")
    assert termination_from_evaluation("TERMINATE", None) == ("terminated", "terminated")
    assert termination_from_evaluation("continue", "n/a") is None
    assert termination_from_evaluation(None, None) is None
