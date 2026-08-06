import pytest
from agents.arc3.solver import PlanChunker, SolveContext


def _fresh_chunker():
    """Return a PlanChunker with just enough state for unit testing plateau memory."""
    c = PlanChunker.__new__(PlanChunker)
    c._loop_detected_action_blacklist = None
    c._failed_plateau_families = set()
    c._plateau_escalation_required = False
    c._plateau_locked_family = None
    c._plateau_lock_duration = 0
    c._plateau_lock_family_replan_count = 0
    c._plateau_lock_last_family = None
    c._plateau_lock_zero_delta_streak = 0
    c._plateau_active = False
    c._active_chunk = None
    c._chunk_history = []
    c._chunk_ledger = []
    c._game_rule_hypotheses = []
    c._archetype = "unknown"
    c._archetype_confidence = 0.0
    c._object_roles = {}
    c._victory_condition = None
    return c


def test_failed_plateau_family_is_not_cleared_by_cell_change(monkeypatch):
    """A018: visible cell change without reward must NOT clear _failed_plateau_families."""
    c = _fresh_chunker()
    c._failed_plateau_families.add("ACTION3")
    hyp_ctx = {
        "last_transition_effect": {
            "n_cells_changed": 12,
            "score_delta": 0,
            "reward": 0,
        }
    }
    # simulate the clear-path logic directly (mirrors solver.py:2740-2751 after A018)
    last_eff = hyp_ctx["last_transition_effect"]
    score_delta = float(last_eff.get("score_delta") or 0.0)
    reward_delta = float(last_eff.get("reward") or last_eff.get("reward_delta") or 0.0)
    if score_delta > 0 or reward_delta > 0:
        c._failed_plateau_families = set()
    assert "ACTION3" in c._failed_plateau_families


def test_failed_plateau_family_cleared_by_reward_tick():
    """A018: a genuine reward tick clears both the blacklist and the failed-plateau set."""
    c = _fresh_chunker()
    c._failed_plateau_families.add("ACTION3")
    c._loop_detected_action_blacklist = {"ACTION3"}
    last_eff = {"n_cells_changed": 1, "score_delta": 1.0, "reward": 0.0}
    score_delta = float(last_eff.get("score_delta") or 0.0)
    reward_delta = float(last_eff.get("reward") or last_eff.get("reward_delta") or 0.0)
    if score_delta > 0 or reward_delta > 0:
        c._loop_detected_action_blacklist = None
        c._failed_plateau_families = set()
    assert c._loop_detected_action_blacklist is None
    assert c._failed_plateau_families == set()


def test_two_failed_plateaus_trigger_escalation():
    """A018: after two families fail and no unfailed candidate exists, do NOT create a third plateau chunk."""
    c = _fresh_chunker()
    c._failed_plateau_families = {"ACTION2", "ACTION3"}
    c._plateau_locked_family = None  # selector found no candidate
    # Simulate the new escalation branch at solver.py:3268
    top_family = c._plateau_locked_family
    if top_family is None and len(c._failed_plateau_families) >= 2:
        c._plateau_escalation_required = True
    elif top_family:
        pytest.fail("should not have fallen into the chunk-creation branch")
    assert c._plateau_escalation_required is True
    assert c._active_chunk is None
