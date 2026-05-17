"""Test A098: Race-safe early-stop guardrails.

Tests that early-stop decisions are prevented when graph evidence shows
useful state transitions or terminal-distance movement in navigation games.
"""

import pytest
from types import SimpleNamespace
from agents.arc3.reasoning_controller import ReasoningController, ReasoningMode
from agents.arc3.world_model import WorldModelGraph


class TestRouteTransitionEvidence:
    """Test detection of route transition evidence."""

    def test_get_route_transition_evidence_with_transitions(self):
        """Graph retrieves route transition evidence."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Record improving transitions
        for step in range(3):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", float(50 - step * 10), 0.0)
            act_id = graph.record_action(step, f"ACTION{step % 2}", {"coordinate": [step, 0]}, state_id)
            graph.record_effect(act_id, obs_id, "distance_improving_move", {
                "step": step,
                "goal_distance": 50.0 - step * 10
            })
        
        # Should be able to query transitions
        effects = graph.get_action_effect_table(limit=10)
        assert len(effects) >= 3

    def test_no_route_evidence_with_only_churn(self):
        """No route evidence when only visual churn."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Record only churn effects
        for step in range(3):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", 42.0, 0.0)
            act_id = graph.record_action(step, f"ACTION{step % 2}", {"coordinate": [step, 0]}, state_id)
            graph.record_effect(act_id, obs_id, "visual_churn", {
                "step": step,
                "goal_distance": 42.0
            })
        
        effects = graph.get_action_effect_table(limit=10)
        assert len(effects) >= 3

    def test_mixed_transitions_and_churn(self):
        """Route evidence detected even with mixed effects."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Mixed effects
        effects_sequence = [
            ("distance_improving_move", 50.0),
            ("visual_churn", 50.0),
            ("distance_improving_move", 40.0),
            ("visual_churn", 40.0),
        ]
        
        for step, (effect_kind, distance) in enumerate(effects_sequence):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", distance, 0.0)
            act_id = graph.record_action(step, f"ACTION{step % 2}", {"coordinate": [step, 0]}, state_id)
            graph.record_effect(act_id, obs_id, effect_kind, {
                "step": step,
                "goal_distance": distance
            })
        
        effects = graph.get_action_effect_table(limit=10)
        assert len(effects) >= 4


class TestRaceSafeGuardrails:
    """Test race-safe early-stop behavior."""

    def test_route_evidence_prevents_exhaustion_decision(self):
        """Route evidence should gate early-stop."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Build a sequence with route evidence
        for step in range(2):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", float(50 - step * 15), 0.0)
            for action_id in ["ACTION1", "ACTION2"]:
                act_id = graph.record_action(step, action_id, {"coordinate": [0, 0]}, state_id)
                graph.record_effect(act_id, obs_id, "distance_improving_move", {
                    "step": step,
                    "goal_distance": 50.0 - step * 15
                })
        
        # Churn evidence
        churn_evidence = graph.get_all_actions_churn_evidence(
            available_actions=["ACTION1", "ACTION2"],
            min_tests_per_action=1
        )
        
        # With route evidence present, exhaustion shouldn't be immediate
        assert churn_evidence is not None

    def test_recent_route_regression_allows_exhaustion_decision(self):
        """Recent regressing route evidence should stop suppressing early-stop."""
        graph = WorldModelGraph("test_task", "test_session")

        for step, distance in enumerate([50.0, 51.0, 52.0, 53.0], start=10):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", distance, 0.0)
            act_id = graph.record_action(step, "ACTION1", {"coordinate": [0, 0]}, state_id)
            graph.record_effect(act_id, obs_id, "distance_regressing_move", {
                "step": step,
                "goal_distance": distance,
                "goal_distance_delta": 1.0,
                "distance_trend": "regressing",
            })

        route_evidence = graph.get_route_transition_evidence(
            available_actions=["ACTION1", "ACTION2"],
            lookback=18,
            limit=8,
        )
        assert route_evidence["has_route_evidence"] is True
        assert route_evidence["has_recent_route_regression"] is True

        controller = ReasoningController({"reasoning_gate": {"route_regression_threshold": 3}})
        controller._consecutive_multi_action_churn_probes = controller._max_multi_action_churn_probes
        compiled_delta = SimpleNamespace(
            step=20,
            failure_signal=None,
            claims=[
                SimpleNamespace(
                    kind="action_effect",
                    effect_class="distance_regressing_move",
                    terminal_alignment="regressing",
                    props={"distance_trend": "regressing"},
                )
            ],
        )
        decision = controller.decide(
            world_summary="",
            compiled_delta=compiled_delta,
            budget_state={
                "route_transition_evidence": route_evidence,
                "world_model_contradiction_count": 0,
                "all_actions_churn_evidence": {},
                "prediction_falsification_counts": {},
            },
            phase="solve",
            active_hypotheses=[],
            available_actions=["ACTION1", "ACTION2"],
            mechanic_priors=[],
            per_action_evidence={
                "ACTION1": {"tested_count": 3, "recent_effects": ["pixel_churn"]},
                "ACTION2": {"tested_count": 3, "recent_effects": ["pixel_churn"]},
            },
        )

        assert decision.mode == ReasoningMode.EARLY_STOP
        assert decision.trigger == "route_regression_exhausted"
        assert decision.world_model_decision == "route_regression_exhausted"

    def test_pure_churn_allows_exhaustion(self):
        """Pure churn still allows exhaustion."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Only churn
        for step in range(2):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", 42.0, 0.0)
            for action_id in ["ACTION1", "ACTION2"]:
                act_id = graph.record_action(step, action_id, {"coordinate": [0, 0]}, state_id)
                graph.record_effect(act_id, obs_id, "visual_churn", {
                    "step": step,
                    "goal_distance": 42.0
                })
        
        churn_evidence = graph.get_all_actions_churn_evidence(
            available_actions=["ACTION1", "ACTION2"],
            min_tests_per_action=2
        )
        
        assert churn_evidence is not None


class TestGuardrailEdgeCases:
    """Test edge cases in guardrail behavior."""

    def test_stale_route_evidence_ignored(self):
        """Route evidence outside lookback window ignored."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Distant history with good evidence
        for step in range(10, 15):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", float(50 - (step-10) * 5), 0.0)
            act_id = graph.record_action(step, "ACTION1", {"coordinate": [0, 0]}, state_id)
            graph.record_effect(act_id, obs_id, "distance_improving_move", {
                "step": step,
                "goal_distance": 50.0 - (step-10) * 5
            })
        
        # Recent churn
        for step in range(20, 22):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", 42.0, 0.0)
            act_id = graph.record_action(step, "ACTION1", {"coordinate": [0, 0]}, state_id)
            graph.record_effect(act_id, obs_id, "visual_churn", {
                "step": step,
                "goal_distance": 42.0
            })

    def test_bounded_transition_window(self):
        """Transition evidence is bounded by lookback window."""
        graph = WorldModelGraph("test_task", "test_session")
        
        # Create effects at various steps
        for step in range(0, 30, 5):
            state_id = graph.record_state(step, f"hash_{step}")
            obs_id = graph.record_observation(step, f"hash_{step}", float(50 - step), 0.0)
            act_id = graph.record_action(step, "ACTION1", {"coordinate": [0, 0]}, state_id)
            graph.record_effect(act_id, obs_id, "distance_improving_move", {
                "step": step,
                "goal_distance": 50.0 - step
            })
        
        effects = graph.get_action_effect_table(limit=10)
        # Should respect limit
        assert len(effects) <= 10
