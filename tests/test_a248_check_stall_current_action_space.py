"""A248: check_stall's "untested actions remain" grace must be computed
against the current cycle's available_actions (a set difference), not a
length subtraction against action_attempt_counts's whole-episode-cumulative
count.

Live evidence (backlog/A248.md): a 100-step smoke run logged
"STALL_CHECK ... available=4, attempted=5, untested=-1 ..." once a
probe-phase ACTION6 click outlived that phase (action_attempt_counts is
never reset) and the environment moved to a goal-directed phase whose
available_actions no longer included ACTION6. The negative "untested" count
is impossible if available/attempted were scoped to the same universe of
actions -- they weren't.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from agents.arc4.annatar_signals import compute_cycle_signals
from agents.arc4.cycle_policy import (
    check_stall,
    count_base_actions,
    stall_threshold,
    untested_remaining_actions,
)
from agents.arc4.types import EvaluationResult, ExecutionResult, PerceptionSnapshot, PlanCandidate, WorkflowDecision, WorkflowState


def _old_check_stall(consecutive_no_progress, max_consecutive_no_progress, num_available_actions, num_attempted_actions):
    """Pre-A248 check_stall body, kept verbatim as the regression oracle.

    No longer reachable from production code (check_stall's signature
    changed) -- used only in this test file to prove the fix is
    behavior-preserving when action-space composition never changes across
    the episode, and to demonstrate the exact bug this card fixes.
    """
    num_available = num_available_actions or 1
    if num_available_actions > 0 and num_available - num_attempted_actions > 0:
        return None
    if consecutive_no_progress >= stall_threshold(max_consecutive_no_progress, num_available_actions):
        return "stall_detected"
    return None


def _perception_snapshot(grid_hash: str = "h1") -> PerceptionSnapshot:
    return PerceptionSnapshot(observation={"grid": grid_hash}, grid_hash=grid_hash)


def _execution_result(action_id: str = "a1") -> ExecutionResult:
    candidate = PlanCandidate(action_id=action_id, goal_id="g1")
    return ExecutionResult(action_id=action_id, candidate=candidate, observation={"grid": "h2"})


def _evaluation_result(*, meaningful_progress: bool, grid_changed: bool) -> EvaluationResult:
    return EvaluationResult(
        decision=WorkflowDecision.CONTINUE,
        meaningful_progress=meaningful_progress,
        metadata={"grid_changed": grid_changed},
    )


class TestNoCompositionChangeRegression:
    """available_actions never changes composition across the episode (the
    common case) -> check_stall's decision must be byte-identical to the
    pre-fix formula's decision, for every combination exercised."""

    AVAILABLE = ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"]

    @classmethod
    def _attempt_keys(cls, n: int) -> set[str]:
        # First n base actions attempted exactly once each; no stale entries.
        return set(cls.AVAILABLE[:n])

    def test_untested_remaining_matches_old_subtraction_when_no_stale_actions(self):
        for n in range(0, len(self.AVAILABLE) + 1):
            attempt_keys = self._attempt_keys(n)
            old_value = len(self.AVAILABLE) - count_base_actions(attempt_keys)
            new_value = untested_remaining_actions(self.AVAILABLE, attempt_keys)
            assert new_value == old_value, f"n={n}"

    def test_check_stall_decisions_match_old_formula_across_combos(self):
        for n in range(0, len(self.AVAILABLE) + 1):
            attempt_keys = self._attempt_keys(n)
            num_attempted = count_base_actions(attempt_keys)
            untested = untested_remaining_actions(self.AVAILABLE, attempt_keys)
            for consecutive_no_progress in (0, 1, 4, 5, 9, 10, 11, 20):
                old_result = _old_check_stall(consecutive_no_progress, 4, len(self.AVAILABLE), num_attempted)
                new_result = check_stall(consecutive_no_progress, 4, len(self.AVAILABLE), untested)
                assert new_result == old_result, f"n={n} consecutive={consecutive_no_progress}"

    def test_action6_coordinate_targets_still_collapse_to_one_base_action(self):
        available = ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION6"]
        attempt_keys = {
            "ACTION1", "ACTION2", "ACTION3", "ACTION4",
            "ACTION6@1,1", "ACTION6@2,2", "ACTION6@3,3",
        }
        assert untested_remaining_actions(available, attempt_keys) == 0
        # Below the two-pass threshold (5*2=10) -> must not stall yet.
        assert check_stall(5, 4, len(available), 0) is None
        assert check_stall(10, 4, len(available), 0) == "stall_detected"

    def test_empty_action_list_unaffected(self):
        assert untested_remaining_actions([], set()) == 0
        assert check_stall(1, 1, 0, 0) is None
        assert check_stall(2, 1, 0, 0) == "stall_detected"


class TestStaleCrossPhaseActions:
    """Reproduces this card's own live scenario: an earlier probe-phase
    ACTION6 click stays in action_attempt_counts (whole-episode-cumulative,
    never reset -- see backlog/A248.md) after the environment moves to a
    later phase whose available_actions no longer include ACTION6."""

    def test_stale_action6_no_longer_available_does_not_go_negative(self):
        # Live log: available=4 (ACTION1-4), attempted (raw base-action
        # count) = 5 (ACTION1-4 + stale ACTION6) -> old formula: 4 - 5 = -1.
        available_actions = ["ACTION1", "ACTION2", "ACTION3", "ACTION4"]
        attempt_keys = {
            "ACTION1", "ACTION2", "ACTION3", "ACTION4",
            "ACTION6@10,20",  # stale: from an earlier probe phase
        }
        old_num_attempted = count_base_actions(attempt_keys)
        assert old_num_attempted == 5  # confirms the bug's precondition
        old_untested = len(available_actions) - old_num_attempted
        assert old_untested == -1  # the exact negative value from the live log

        new_untested = untested_remaining_actions(available_actions, attempt_keys)
        assert new_untested == 0  # non-negative, and correct: every *current* action tried
        assert new_untested >= 0

    def test_stale_action_scenario_still_stalls_once_current_actions_are_all_tried(self):
        available_actions = ["ACTION1", "ACTION2", "ACTION3", "ACTION4"]
        attempt_keys = {"ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION6@10,20"}
        untested = untested_remaining_actions(available_actions, attempt_keys)
        assert untested == 0
        threshold = stall_threshold(4, len(available_actions))  # max(4, 4*2) = 8
        assert check_stall(threshold - 1, 4, len(available_actions), untested) is None
        assert check_stall(threshold, 4, len(available_actions), untested) == "stall_detected"

    def test_stale_action_scenario_grants_early_pass_when_a_current_action_is_genuinely_untested(self):
        # Same stale-ACTION6-entry shape, but ACTION4 in the *current* phase
        # has never actually been attempted -- must not stall regardless of
        # how high consecutive_no_progress climbs.
        available_actions = ["ACTION1", "ACTION2", "ACTION3", "ACTION4"]
        attempt_keys = {"ACTION1", "ACTION2", "ACTION3", "ACTION6@10,20"}

        old_num_attempted = count_base_actions(attempt_keys)  # 4 (collapsed) -- masks the real gap
        old_untested = len(available_actions) - old_num_attempted
        assert old_untested == 0  # old formula wrongly says nothing untested remains

        new_untested = untested_remaining_actions(available_actions, attempt_keys)
        assert new_untested == 1  # ACTION4 is genuinely untested this phase

        assert check_stall(100, 4, len(available_actions), new_untested) is None
        # Confirm the old formula would have (wrongly) allowed a stall here --
        # this is the actual behavioral risk backlog/A248.md describes, not
        # just a cosmetic log artifact.
        assert _old_check_stall(100, 4, len(available_actions), old_num_attempted) == "stall_detected"


class TestStallReasonOverrideOnlyFiresWhenGenuinelySupported:
    """annatar_signals.compute_cycle_signals's stall_reason override
    (all_falsified=True, untested_remaining=False -- A202's deliberate,
    unchanged design) must only fire when the stall_reason it's handed
    genuinely reflects the *current* action space, not an artifact of stale
    cross-phase action_attempt_counts data feeding a wrong check_stall
    input. This test reproduces A248's exact live scenario end-to-end."""

    AVAILABLE_ACTIONS = ["ACTION1", "ACTION2", "ACTION3", "ACTION4"]
    ATTEMPT_KEYS = {"ACTION1", "ACTION2", "ACTION3", "ACTION6@10,20"}  # ACTION4 genuinely untested

    def _signals(self, *, stall_reason):
        graph_port = MagicMock()
        graph_port.fetch_untested_actions.return_value = []
        return compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=True),
            anchor_ref="g1",
            anchor_type="goal",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=graph_port,
            stall_reason=stall_reason,
        )

    def test_old_pipeline_would_have_wrongly_forced_exhaustion(self):
        # The old, buggy pipeline: raw count_base_actions subtraction feeds
        # check_stall directly.
        old_num_attempted = count_base_actions(self.ATTEMPT_KEYS)
        old_stall_reason = _old_check_stall(100, 4, len(self.AVAILABLE_ACTIONS), old_num_attempted)
        assert old_stall_reason == "stall_detected"  # the bug: fires even though ACTION4 is untested

        signals = self._signals(stall_reason=old_stall_reason)
        assert signals.all_falsified is True
        assert signals.untested_remaining is False

    def test_fixed_pipeline_does_not_force_exhaustion_while_action4_is_untested(self):
        # The fixed pipeline: untested_remaining_actions (set difference)
        # feeds check_stall.
        untested = untested_remaining_actions(self.AVAILABLE_ACTIONS, self.ATTEMPT_KEYS)
        new_stall_reason = check_stall(100, 4, len(self.AVAILABLE_ACTIONS), untested)
        assert new_stall_reason is None  # fixed: ACTION4 genuinely untested -> no stall

        signals = self._signals(stall_reason=new_stall_reason)
        # Without the (correctly absent) stall override, defaults from the
        # graph_port mock (empty fetch_untested_actions) stand: nothing
        # forces all_falsified/untested_remaining toward exhaustion.
        assert signals.all_falsified is False
        assert signals.untested_remaining is False
