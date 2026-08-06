from types import SimpleNamespace

from agents.arc3.reasoning_controller import ReasoningController, ReasoningMode
from agents.arc3.world_model import WorldModelGraph
from agents.arc3.world_model_planner import WorldModelPlanner


def _record_route_effect(graph, step, action_id, effect_class, delta):
    state_id = graph.record_state(step, f"hash-{step}")
    obs_id = graph.record_observation(step, f"hash-{step}", 0.0, 0.0)
    act_id = graph.record_action(step, action_id, {}, state_id)
    graph.record_effect(
        act_id,
        obs_id,
        effect_class,
        {
            "step": step,
            "goal_distance_delta": delta,
            "distance_trend": "improving" if delta < 0 else "regressing",
        },
    )


def test_distance_improving_route_counts_as_prediction_progress():
    graph = WorldModelGraph("task", "session")
    _record_route_effect(graph, 1, "ACTION4", "distance_improving_move", -4.0)

    evidence = graph.get_action_prediction_evidence("ACTION4")

    assert evidence["effect_histogram"]["distance_improving_move"] == 1
    assert evidence["meaningful_progress_rate"] == 1.0


def test_route_candidate_drops_action_after_latest_regression():
    graph = WorldModelGraph("task", "session")
    _record_route_effect(graph, 1, "ACTION4", "distance_improving_move", -4.0)
    _record_route_effect(graph, 2, "ACTION4", "distance_improving_move", -4.0)
    _record_route_effect(graph, 3, "ACTION4", "distance_regressing_move", 3.0)
    _record_route_effect(graph, 4, "ACTION1", "distance_improving_move", -2.0)

    candidates = graph.find_route_candidates(
        available_actions=["ACTION1", "ACTION4"],
        max_depth=4,
        limit=5,
    )

    assert [c["action_id"] for c in candidates] == ["ACTION1"]
    assert candidates[0]["latest_distance_delta"] == -2.0


def test_planner_marks_route_prediction_as_meaningful_progress():
    graph = WorldModelGraph("task", "session")
    _record_route_effect(graph, 1, "ACTION4", "distance_improving_move", -4.0)
    planner = WorldModelPlanner({})

    selection = planner.select_next_candidate(
        world_model=graph,
        mechanic_priors=[],
        available_actions=["ACTION1", "ACTION4"],
        budget_state={},
    )

    assert selection.selected.action_id == "ACTION4"
    assert selection.selected.predicted_observation["meaningful_progress"] is True
    assert selection.selected.predicted_observation["progress_class"] == "route_progress"


def test_controller_follow_graph_route_even_with_active_goal_hypothesis():
    controller = ReasoningController({})
    compiled_delta = SimpleNamespace(
        step=8,
        failure_signal=None,
        claims=[
            SimpleNamespace(
                kind="action_effect",
                effect_class="distance_improving_move",
                terminal_alignment="unknown",
                props={"distance_trend": "improving"},
            )
        ],
    )

    decision = controller.decide(
        world_summary="",
        compiled_delta=compiled_delta,
        budget_state={
            "route_transition_evidence": {
                "has_route_evidence": True,
                "improving_transition_count": 2,
                "best_distance_delta": -4.0,
                "recent_regression_streak": 0,
            },
            "active_goal_type": "endpoint_connection",
            "active_goal_confidence": 0.7,
        },
        phase="solve",
        active_hypotheses=[],
        available_actions=["ACTION1", "ACTION2", "ACTION4"],
        mechanic_priors=[],
        per_action_evidence={
            "ACTION1": {"tested_count": 1, "recent_effects": ["pixel_churn"]},
            "ACTION2": {"tested_count": 1, "recent_effects": ["pixel_churn"]},
            "ACTION4": {"tested_count": 1, "recent_effects": ["distance_improving_move"]},
        },
    )

    assert decision.mode == ReasoningMode.CHEAP_PROBE
    assert decision.trigger == "graph_route_follow"
    assert decision.world_model_decision == "follow_graph_route"
