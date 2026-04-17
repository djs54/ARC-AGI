import pytest
from unittest.mock import MagicMock
from agents.arc3.hypothesis import (
    StateGraph, StateNode, Transition, InvariantDetector, HypothesisManager, Hypothesis
)

# ── StateGraph tests ─────────────────────────────────────────

def test_add_state_new_returns_true():
    graph = StateGraph()
    node = StateNode("hash1", 1, {}, 1.0, [[1]])
    assert graph.add_state(node) is True
    assert "hash1" in graph.nodes

def test_add_state_revisit_returns_false():
    graph = StateGraph()
    node1 = StateNode("hash1", 1, {}, 1.0, [[1]])
    graph.add_state(node1)
    node2 = StateNode("hash1", 2, {}, 1.0, [[1]])
    assert graph.add_state(node2) is False
    assert len(graph.nodes) == 1

def test_detect_loop_on_revisit():
    graph = StateGraph()
    graph.add_state(StateNode("h1", 1, {}, 1.0, [[1]]))
    graph.add_state(StateNode("h2", 2, {}, 1.0, [[2]]))
    assert graph.detect_loop() is None
    graph.add_state(StateNode("h1", 3, {}, 1.0, [[1]]))
    assert graph.detect_loop() == "h1"

def test_no_loop_on_unique_states():
    graph = StateGraph()
    graph.add_state(StateNode("h1", 1, {}, 1.0, [[1]]))
    graph.add_state(StateNode("h2", 2, {}, 1.0, [[2]]))
    graph.add_state(StateNode("h3", 3, {}, 1.0, [[3]]))
    assert graph.detect_loop() is None

def test_get_unexplored_actions():
    graph = StateGraph()
    graph.add_state(StateNode("h1", 1, {}, 1.0, [[1]]))
    graph.add_transition(Transition("h1", "h2", "ACTION1", 1, "", 1, []))
    unexplored = graph.get_unexplored_actions("h1", ["ACTION1", "ACTION2", "ACTION3"])
    assert unexplored == ["ACTION2", "ACTION3"]

def test_get_action_effects_across_states():
    graph = StateGraph()
    graph.add_transition(Transition("h1", "h2", "ACTION1", 1, "diff1", 10, []))
    graph.add_transition(Transition("h3", "h4", "ACTION1", 2, "diff2", 12, []))
    effects = graph.get_action_effects("ACTION1")
    assert len(effects) == 2
    assert effects[0].diff_summary == "diff1"

def test_clear_resets_all():
    graph = StateGraph()
    graph.add_state(StateNode("h1", 1, {}, 1.0, [[1]]))
    graph.add_transition(Transition("h1", "h2", "A1", 1, "", 1, []))
    graph.clear()
    assert len(graph.nodes) == 0
    assert len(graph.edges) == 0
    assert graph._visit_order == []

# ── InvariantDetector tests ──────────────────────────────────

def test_find_static_rows_with_3_frames():
    detector = InvariantDetector(min_frames=3)
    detector.add_frame([[1, 1], [2, 2]])
    detector.add_frame([[1, 1], [3, 3]])
    detector.add_frame([[1, 1], [4, 4]])
    static = detector.find_static_rows()
    assert static == [0] # Row 0 is [1, 1] in all

def test_find_static_rows_insufficient_frames():
    detector = InvariantDetector(min_frames=3)
    detector.add_frame([[1, 1], [2, 2]])
    detector.add_frame([[1, 1], [2, 2]])
    assert detector.find_static_rows() == []

def test_find_dynamic_regions():
    detector = InvariantDetector()
    detector.add_frame([[0, 0, 0], [0, 0, 0]])
    detector.add_frame([[0, 1, 0], [0, 0, 0]])
    regions = detector.find_dynamic_regions()
    assert len(regions) == 1
    assert regions[0]["rows"] == [0]
    assert regions[0]["cols"] == [1]

def test_estimate_hud_rows_bottom_10pct():
    detector = InvariantDetector(min_frames=2)
    # 20 rows
    grid = [[0]*10 for _ in range(20)]
    detector.add_frame(grid)
    detector.add_frame(grid)
    # HUD candidate must be in bottom 10% (rows 18, 19)
    detector.add_frame(grid)
    static = detector.find_static_rows()
    assert 19 in static
    hud = detector.estimate_hud_rows()
    assert 19 in hud
    assert 0 not in hud

# ── HypothesisManager tests ─────────────────────────────────

@pytest.mark.asyncio
async def test_observe_creates_state_node():
    mgr = HypothesisManager(MagicMock(), "session1")
    await mgr.observe([[[1]]], None, 1, ["A1"], {})
    assert "h1" in mgr.graph.nodes or len(mgr.graph.nodes) == 1

@pytest.mark.asyncio
async def test_observe_records_transition():
    mgr = HypothesisManager(MagicMock(), "session1")
    await mgr.observe([[[1]]], None, 1, ["A1"], {})
    await mgr.observe([[[2]]], "A1", 2, ["A1"], {})
    assert len(mgr.graph.edges) == 1
    assert mgr.graph.edges[list(mgr.graph.nodes.keys())[0]][0].action == "A1"

@pytest.mark.asyncio
async def test_hypothesis_generated_from_transition():
    mgr = HypothesisManager(MagicMock(), "session1")
    await mgr.observe([[[1]]], None, 1, ["A1"], {})
    await mgr.observe([[[2]]], "A1", 2, ["A1"], {})
    assert "action-A1" in mgr.hypotheses
    assert "localized_change" in mgr.hypotheses["action-A1"].description

@pytest.mark.asyncio
async def test_wall_hypothesis_on_zero_change():
    mgr = HypothesisManager(MagicMock(), "session1")
    await mgr.observe([[[1]]], None, 1, ["A1"], {})
    await mgr.observe([[[1]]], "A1", 2, ["A1"], {})
    # Should have action-A1 AND a wall hypothesis
    assert any(h.id.startswith("wall-") for h in mgr.hypotheses.values())

def test_confidence_update_supports():
    hyp = Hypothesis("h1", "desc", "cat")
    hyp.update(supports=True)
    assert hyp.confidence == 1.0
    assert hyp.support_count == 1

def test_confidence_update_contradicts():
    hyp = Hypothesis("h1", "desc", "cat")
    hyp.update(supports=False)
    assert hyp.confidence == 0.0
    assert hyp.contradiction_count == 1

def test_auto_confirm_at_threshold():
    hyp = Hypothesis("h1", "desc", "cat")
    # MIN_EVIDENCE=3, CONFIRM_THRESHOLD=0.8
    hyp.update(True)
    hyp.update(True)
    hyp.update(True)
    assert hyp.status == "confirmed"

def test_auto_prune_at_threshold():
    hyp = Hypothesis("h1", "desc", "cat")
    # MIN_EVIDENCE=3, PRUNE_THRESHOLD=0.2
    hyp.update(False)
    hyp.update(False)
    hyp.update(False)
    assert hyp.status == "pruned"

def test_explore_policy_when_low_confirmation():
    mgr = HypothesisManager(MagicMock(), "session1")
    mgr.hypotheses["h1"] = Hypothesis("h1", "d", "cat", status="active")
    assert mgr._decide_policy(1.0, {"untested_count": 0, "top_two_low_value": False}) == "explore"

def test_exploit_policy_when_low_energy():
    mgr = HypothesisManager(MagicMock(), "session1")
    # EXPLORE_ENERGY_FLOOR = 0.3, but low energy should only exploit with a high-confidence hypothesis
    mgr.hypotheses["h1"] = Hypothesis("h1", "d", "cat", confidence=0.9, status="confirmed")
    assert mgr._decide_policy(0.2, {"untested_count": 0, "top_two_low_value": False}) == "exploit"

def test_low_energy_without_high_confidence_hypothesis_stays_explore():
    mgr = HypothesisManager(MagicMock(), "session1")
    assert mgr.energy_policy(0.2, {"untested_count": 0, "top_two_low_value": False}) == "explore"

def test_policy_stays_explore_until_action_coverage_complete():
    mgr = HypothesisManager(MagicMock(), "session1")
    assert mgr._decide_policy(0.2, {"untested_count": 3, "top_two_low_value": False}) == "explore"

def test_policy_returns_to_explore_when_top_actions_decay():
    mgr = HypothesisManager(MagicMock(), "session1")
    assert mgr._decide_policy(1.0, {"untested_count": 0, "top_two_low_value": True}) == "explore"

@pytest.mark.asyncio
async def test_distill_flushes_confirmed_to_brain():
    brain = MagicMock()
    
    async def mock_notify_turn(**kwargs):
        return {"status": "ok"}
    
    brain.notify_turn = mock_notify_turn

    mgr = HypothesisManager(brain, "session1")
    mgr.hypotheses["h1"] = Hypothesis("h1", "d", "cat", status="confirmed")
    count = await mgr.distill_to_brain()
    assert count == 1

@pytest.mark.asyncio
async def test_distill_flushes_pruned_to_brain():
    brain = MagicMock()

    async def mock_notify_turn(**kwargs):
        return {"status": "ok"}

    brain.notify_turn = mock_notify_turn

    mgr = HypothesisManager(brain, "session1")
    mgr.hypotheses["h1"] = Hypothesis("h1", "d", "cat", status="pruned")
    count = await mgr.distill_to_brain()
    assert count == 1

@pytest.mark.asyncio
async def test_generate_hypotheses_alias_returns_observe_context():
    mgr = HypothesisManager(MagicMock(), "session1")
    ctx = await mgr.generate_hypotheses([[[1]]], None, 1, ["A1"], {"grid": [[[1]]], "colors": []})
    assert "action_facts" in ctx
    assert "path_hypotheses" in ctx

def test_get_best_hypothesis_prefers_high_confidence():
    mgr = HypothesisManager(MagicMock(), "session1")
    mgr.hypotheses["low"] = Hypothesis("low", "low", "rule", confidence=0.4, status="active", value_score=0.2)
    mgr.hypotheses["high"] = Hypothesis("high", "high", "rule", confidence=0.9, status="confirmed", value_score=0.8)
    best = mgr.get_best_hypothesis()
    assert best["id"] == "high"
    assert best["confidence"] == 0.9

@pytest.mark.asyncio
async def test_get_exploration_action_prefers_unexplored_current_state_action():
    mgr = HypothesisManager(MagicMock(), "session1")
    await mgr.observe([[[1]]], None, 1, ["A1", "A2"], {"grid": [[[1]]], "colors": []})
    await mgr.observe([[[2]]], "A1", 2, ["A1", "A2"], {"grid": [[[2]]], "colors": []}, transition_meta={"reward": 0.0, "state_after": "NOT_FINISHED"})
    current_hash = mgr._prev_state_hash
    mgr.graph.add_transition(Transition(current_hash, "next", "A1", 3, "no_visible_change: no pixels changed", 0, []))
    assert mgr.get_exploration_action(["A1", "A2"]) == "A2"

def test_reset_graph_preserves_hypotheses():
    mgr = HypothesisManager(MagicMock(), "session1")
    mgr.hypotheses["h1"] = Hypothesis("h1", "d", "cat")
    mgr.graph.add_state(StateNode("h1", 1, {}, 1.0, [[1]]))
    mgr.reset_graph()
    assert len(mgr.graph.nodes) == 0
    assert "h1" in mgr.hypotheses

def test_energy_from_hud_estimation():
    mgr = HypothesisManager(MagicMock(), "session1")
    # partial bar: row of 10, 5 non-zero
    grid_2d = [[0]*10 for _ in range(10)]
    grid_2d[9] = [1, 1, 1, 1, 1, 0, 0, 0, 0, 0]
    hud_rows = [9]
    energy = mgr._estimate_energy_from_hud(hud_rows, grid_2d)
    assert energy == 0.5

@pytest.mark.asyncio
async def test_loop_detection_in_observe_output():
    mgr = HypothesisManager(MagicMock(), "session1")
    await mgr.observe([[[1]]], None, 1, ["A1"], {})
    await mgr.observe([[[2]]], "A1", 2, ["A1"], {})
    res = await mgr.observe([[[1]]], "A1", 3, ["A1"], {})
    assert res["loop_detected"] is True

@pytest.mark.asyncio
async def test_observe_returns_action_effects_and_last_transition():
    mgr = HypothesisManager(MagicMock(), "session1")
    await mgr.observe([[[1, 1], [1, 1]]], None, 1, ["A1", "A2"], {})
    res = await mgr.observe(
        [[[1, 2], [1, 1]]],
        "A1",
        2,
        ["A1", "A2"],
        {},
        transition_meta={"reward": 0.0, "state_after": "NOT_FINISHED"},
    )
    action_effects = {item["action"]: item for item in res["observed_action_effects"]}
    assert action_effects["A1"]["times_seen"] == 1
    assert action_effects["A1"]["avg_meaningful_change"] > 0.0
    assert action_effects["A1"]["last_meaningful_label"] == "low_value"
    assert "rank_score" in action_effects["A1"]
    assert "retest_budget" in action_effects["A1"]
    assert action_effects["A1"]["recent_diff"].startswith("localized_change")
    assert action_effects["A2"]["recent_diff"] == "UNTESTED"
    assert res["last_transition_effect"]["action"] == "A1"
    assert res["last_transition_effect"]["meaningful_change_label"] == "low_value"

@pytest.mark.asyncio
async def test_consistent_low_value_action_does_not_become_confirmed():
    mgr = HypothesisManager(MagicMock(), "session1")
    await mgr.observe([[[1, 1, 1]]], None, 1, ["A1"], {})
    await mgr.observe([[[1, 1, 2]]], "A1", 2, ["A1"], {}, transition_meta={"reward": 0.0, "state_after": "NOT_FINISHED"})
    await mgr.observe([[[1, 2, 2]]], "A1", 3, ["A1"], {}, transition_meta={"reward": 0.0, "state_after": "NOT_FINISHED"})
    await mgr.observe([[[2, 2, 2]]], "A1", 4, ["A1"], {}, transition_meta={"reward": 0.0, "state_after": "NOT_FINISHED"})
    hyp = mgr.hypotheses["action-A1"]
    assert hyp.effect_consistency > 0.0
    assert hyp.value_status in {"low_value", "ineffective", "tentative"}
    assert hyp.status != "confirmed"

@pytest.mark.asyncio
async def test_path_hypotheses_are_returned_after_multiple_steps():
    mgr = HypothesisManager(MagicMock(), "session1")
    await mgr.observe([[[1, 1, 1]]], None, 1, ["A1", "A2"], {})
    await mgr.observe([[[1, 1, 2]]], "A1", 2, ["A1", "A2"], {}, transition_meta={"reward": 0.0, "state_after": "NOT_FINISHED"})
    res = await mgr.observe([[[1, 2, 2]]], "A2", 3, ["A1", "A2"], {}, transition_meta={"reward": 0.0, "state_after": "NOT_FINISHED"})
    assert res["path_hypotheses"]
    assert res["path_hypotheses"][0]["actions"] == ["A1", "A2"]

@pytest.mark.asyncio
async def test_path_hypotheses_3_step():
    mgr = HypothesisManager(MagicMock(), "session1")
    await mgr.observe([[[1]]], None, 1, ["A1", "A2", "A3"], {})
    await mgr.observe([[[2]]], "A1", 2, ["A1", "A2", "A3"], {}, transition_meta={"reward": 0.0, "state_after": "S2"})
    await mgr.observe([[[3]]], "A2", 3, ["A1", "A2", "A3"], {}, transition_meta={"reward": 0.0, "state_after": "S3"})
    res = await mgr.observe([[[4]]], "A3", 4, ["A1", "A2", "A3"], {}, transition_meta={"reward": 1.0, "state_after": "S4"})
    
    paths = res["path_hypotheses"]
    # Should have a 3-step path [A1, A2, A3] and likely a 2-step [A2, A3]
    three_step = next((p for p in paths if len(p["actions"]) == 3), None)
    assert three_step is not None
    assert three_step["actions"] == ["A1", "A2", "A3"]
    assert three_step["value_status"] == "valuable"

@pytest.mark.asyncio
async def test_path_hypothesis_detects_loop_to_start():
    mgr = HypothesisManager(MagicMock(), "session1")
    await mgr.observe([[[1]]], None, 1, ["A1", "A2"], {})
    await mgr.observe([[[2]]], "A1", 2, ["A1", "A2"], {}, transition_meta={"reward": 0.0})
    res = await mgr.observe([[[1]]], "A2", 3, ["A1", "A2"], {}, transition_meta={"reward": 0.0})
    
    path_hyp = res["path_hypotheses"][0]
    assert "loop" in path_hyp["description"].lower()
    assert path_hyp["value_status"] == "ineffective"

@pytest.mark.asyncio
async def test_action_fact_promotion_returns_compact_fact():
    mgr = HypothesisManager(MagicMock(), "session1")
    await mgr.observe([[[1, 1], [1, 1]]], None, 1, ["A1"], {})
    await mgr.observe([[[1, 1], [1, 2]]], "A1", 2, ["A1"], {}, transition_meta={"reward": 0.0, "state_after": "NOT_FINISHED"})
    await mgr.observe([[[1, 2], [1, 2]]], "A1", 3, ["A1"], {}, transition_meta={"reward": 0.0, "state_after": "NOT_FINISHED"})
    res = await mgr.observe([[[2, 2], [1, 2]]], "A1", 4, ["A1"], {}, transition_meta={"reward": 0.0, "state_after": "NOT_FINISHED"})
    assert res["action_facts"]
    fact = res["action_facts"][0]
    assert fact["action"] == "A1"
    assert fact["fact_type"] in {"deterministic_effect", "low_value", "repeatable_effect"}
    assert fact["evidence_count"] >= 1
    assert fact["description"]
    assert "trend" in fact

@pytest.mark.asyncio
async def test_last_transition_effect_includes_board_snapshots():
    mgr = HypothesisManager(MagicMock(), "session1")
    await mgr.observe([[[1, 1], [1, 1]]], None, 1, ["A1"], {})
    res = await mgr.observe(
        [[[1, 2], [1, 1]]],
        "A1",
        2,
        ["A1"],
        {},
        transition_meta={"reward": 0.0, "state_after": "NOT_FINISHED"},
    )
    effect = res["last_transition_effect"]
    assert effect["before_snapshot"]["rows"] == 2
    assert effect["after_snapshot"]["rows"] == 2
    assert "coarse_map" in effect["before_snapshot"]
    assert effect["before_frame_hash"]
    assert effect["after_frame_hash"]
    assert effect["changed_region"]["before_crop"]
    assert effect["changed_region"]["after_crop"]
    assert effect["changed_region"]["row_range"] == [0, 1]

@pytest.mark.asyncio
async def test_low_value_component_path_is_not_promoted_to_tentative():
    mgr = HypothesisManager(MagicMock(), "session1")
    await mgr.observe([[[1, 1], [1, 1]]], None, 1, ["A1", "A2"], {})
    await mgr.observe(
        [[[1, 2], [1, 1]]],
        "A1",
        2,
        ["A1", "A2"],
        {},
        transition_meta={"reward": 0.0, "state_after": "NOT_FINISHED"},
    )
    res = await mgr.observe(
        [[[1, 2], [2, 1]]],
        "A2",
        3,
        ["A1", "A2"],
        {},
        transition_meta={"reward": 0.0, "state_after": "NOT_FINISHED"},
    )
    assert res["action_facts"][0]["value_status"] == "low_value"
    assert res["path_hypotheses"][0]["value_status"] == "low_value"

@pytest.mark.asyncio
async def test_detects_single_blocked_action_environment_bottleneck():
    mgr = HypothesisManager(MagicMock(), "session1")
    await mgr.observe([[[1, 1]]], None, 1, ["A6"], {})
    await mgr.observe([[[1, 1]]], "A6", 2, ["A6"], {}, transition_meta={"reward": 0.0, "state_after": "NOT_FINISHED"})
    res = await mgr.observe([[[1, 1]]], "A6", 3, ["A6"], {}, transition_meta={"reward": 0.0, "state_after": "NOT_FINISHED"})
    bottleneck = res["environment_bottleneck"]
    assert bottleneck["type"] == "single_blocked_action"
    assert bottleneck["action"] == "A6"
    assert "blocked/no-op" in bottleneck["message"]

@pytest.mark.asyncio
async def test_blocked_action_fact_promotes_no_op():
    mgr = HypothesisManager(MagicMock(), "session1")
    await mgr.observe([[[1, 1]]], None, 1, ["A1"], {})
    await mgr.observe([[[1, 1]]], "A1", 2, ["A1"], {}, transition_meta={"reward": 0.0, "state_after": "NOT_FINISHED"})
    await mgr.observe([[[1, 1]]], "A1", 3, ["A1"], {}, transition_meta={"reward": 0.0, "state_after": "NOT_FINISHED"})
    res = await mgr.observe([[[1, 1]]], "A1", 4, ["A1"], {}, transition_meta={"reward": 0.0, "state_after": "NOT_FINISHED"})
    fact = res["action_facts"][0]
    assert fact["fact_type"] == "blocked"
    assert "is blocked" in fact["description"]

def test_meaningful_change_marks_large_novel_effect_as_tentative_progress():
    mgr = HypothesisManager(MagicMock(), "session1")
    result = mgr._evaluate_meaningful_change(
        diff={"pixels_changed": 52},
        reward=0.0,
        is_new_state=True,
        looped=False,
        final_state="NOT_FINISHED",
    )
    assert result["score"] >= 0.35
    assert result["label"] == "tentative_progress"

def test_meaningful_change_decays_after_repeated_zero_reward_attempts():
    mgr = HypothesisManager(MagicMock(), "session1")
    result = mgr._evaluate_meaningful_change(
        diff={"pixels_changed": 52},
        reward=0.0,
        is_new_state=True,
        looped=False,
        final_state="NOT_FINISHED",
        prior_zero_reward_streak=3,
    )
    assert result["score"] < 0.35
    assert result["label"] in {"low_value", "no_progress"}
    assert "repeat_zero_reward_decay" in result["reasons"]
    assert result["zero_reward_streak"] == 4

def test_meaningful_change_penalizes_loop_and_no_change():
    mgr = HypothesisManager(MagicMock(), "session1")
    result = mgr._evaluate_meaningful_change(
        diff={"pixels_changed": 0},
        reward=0.0,
        is_new_state=False,
        looped=True,
        final_state="NOT_FINISHED",
    )
    assert result["score"] == 0.0
    assert result["label"] == "no_progress"
    assert "loop_penalty" in result["reasons"]
    assert "no_visible_change" in result["reasons"]

def test_count_zero_reward_streak_stops_on_reward():
    mgr = HypothesisManager(MagicMock(), "session1")
    effects = [
        Transition("h1", "h2", "ACTION1", 1, "d1", 5, [], reward_signal=0.0),
        Transition("h2", "h3", "ACTION1", 2, "d2", 5, [], reward_signal=0.0),
        Transition("h3", "h4", "ACTION1", 3, "d3", 5, [], reward_signal=0.5),
        Transition("h4", "h5", "ACTION1", 4, "d4", 5, [], reward_signal=0.0),
    ]
    assert mgr._count_zero_reward_streak(effects) == 1

def test_compute_diff_accuracy():
    mgr = HypothesisManager(MagicMock(), "session1")
    prev = [[1, 1], [1, 1]]
    curr = [[1, 2], [1, 1]]
    diff = mgr._compute_diff(prev, curr)
    assert diff["pixels_changed"] == 1
    assert diff["effect_kind"] == "localized_change"
    assert "rows 0-0, cols 1-1" in diff["summary"]
    assert diff["bbox"] == {"row_start": 0, "row_end": 0, "col_start": 1, "col_end": 1}
    assert diff["center"] == {"row": 0.0, "col": 1.0}

@pytest.mark.asyncio
async def test_action_fact_detects_leftward_drift_trend():
    mgr = HypothesisManager(MagicMock(), "session1")
    await mgr.observe([[[0, 0, 0, 7, 7, 7]]], None, 1, ["A6"], {})
    await mgr.observe([[[0, 0, 0, 7, 7, 4]]], "A6", 2, ["A6"], {}, transition_meta={"reward": 0.0, "state_after": "NOT_FINISHED"})
    await mgr.observe([[[0, 0, 0, 7, 4, 4]]], "A6", 3, ["A6"], {}, transition_meta={"reward": 0.0, "state_after": "NOT_FINISHED"})
    res = await mgr.observe([[[0, 0, 0, 4, 4, 4]]], "A6", 4, ["A6"], {}, transition_meta={"reward": 0.0, "state_after": "NOT_FINISHED"})
    fact = next(fact for fact in res["action_facts"] if fact["action"] == "A6")
    assert fact["trend"] is not None
    assert fact["trend"]["kind"] == "directional_drift"
    assert fact["trend"]["direction"] == "left"
    assert "leftward drift" in fact["description"]

@pytest.mark.asyncio
async def test_compact_exploration():
    mgr = HypothesisManager(MagicMock(), "session1")
    # Setup some state
    await mgr.observe([[[1]]], None, 1, ["A1"], {})
    await mgr.observe([[[1]]], "A1", 2, ["A1"], {}, transition_meta={"reward": 0.0})
    await mgr.observe([[[1]]], "A1", 3, ["A1"], {}, transition_meta={"reward": 0.0})
    
    mgr.hypotheses["h1"] = Hypothesis("h1", "rule confirmed", "rule", status="confirmed")
    mgr.hypotheses["h2"] = Hypothesis("h2", "rule refuted", "rule", status="refuted")
    
    compaction = mgr.compact_exploration(current_step=3)
    
    assert compaction.timestamp_step == 3
    assert "A1" in compaction.action_summaries
    assert "A1: no pixels changed" in compaction.action_summaries["A1"]
    assert "rule confirmed" in compaction.confirmed_rules
    assert "rule refuted" in compaction.refuted_rules
    # Loop detection check
    assert len(compaction.known_loops) >= 1
    assert "A1" in compaction.known_loops[0]
