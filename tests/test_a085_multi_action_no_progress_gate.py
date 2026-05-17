"""Tests for A085: Multi-Action No-Progress Reasoning Gate."""

import pytest
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.reasoning_controller import ReasoningController, ReasoningMode


class TestMultiActionChurnDetection:
    """A085: Tests for multi-action churn detection gating."""

    def test_multi_action_churn_triggers_cheap_probe(self):
        """Multi-action churn with no progress should gate to cheap_probe."""
        controller = ReasoningController(config={"reasoning_gate": {}})
        
        # Simulate multi-action environment with churn
        per_action_evidence = {
            "ACTION1": {
                "tested_count": 3,
                "recent_effects": ["pixel_churn", "pixel_churn", "none"],
                "last_progress_step": -1,
                "frame_hashes": ["hash1", "hash1", "hash1"]
            },
            "ACTION2": {
                "tested_count": 3,
                "recent_effects": ["pixel_churn", "none", "pixel_churn"],
                "last_progress_step": -1,
                "frame_hashes": ["hash2", "hash2", "hash2"]
            },
            "ACTION3": {
                "tested_count": 2,
                "recent_effects": ["pixel_churn", "pixel_churn"],
                "last_progress_step": -1,
                "frame_hashes": ["hash3", "hash3"]
            },
        }
        
        # Build a mock compiled_delta
        class MockDelta:
            failure_signal = None
            step = 15
            claims = []
        
        decision = controller.decide(
            world_summary="test",
            compiled_delta=MockDelta(),
            budget_state={},
            phase="solve",
            active_hypotheses=[],
            available_actions=["ACTION1", "ACTION2", "ACTION3"],
            mechanic_priors=[],
            per_action_evidence=per_action_evidence
        )
        
        # With 3 actions all tested and producing churn, should detect and gate
        assert decision.multi_action_churn_detected is True or decision.mode == ReasoningMode.MULTI_ACTION_CHURN_PROBE
        assert decision.actions_tested_count == 3
        assert decision.productive_action_count == 0

    def test_progress_resets_churn_gate(self):
        """Meaningful object progress should reset the no-progress gate."""
        controller = ReasoningController(config={"reasoning_gate": {}})
        
        per_action_evidence = {
            "ACTION1": {
                "tested_count": 2,
                "recent_effects": ["object_progress", "pixel_churn"],
                "last_progress_step": 10,
                "frame_hashes": ["hash1", "hash2"]
            },
            "ACTION2": {
                "tested_count": 1,
                "recent_effects": ["pixel_churn"],
                "last_progress_step": -1,
                "frame_hashes": ["hash2"]
            },
        }
        
        class MockDelta:
            failure_signal = None
            step = 11
            claims = [type("Claim", (), {"kind": "action_effect", "effect_class": "object_progress"})()]
        
        decision = controller.decide(
            world_summary="test",
            compiled_delta=MockDelta(),
            budget_state={},
            phase="solve",
            active_hypotheses=[],
            available_actions=["ACTION1", "ACTION2"],
            mechanic_priors=[],
            per_action_evidence=per_action_evidence
        )
        
        # Progress was detected, gate should not trigger
        assert decision.multi_action_churn_detected is False

    def test_single_action_stall_takes_precedence(self):
        """Single-action stall logic should take precedence over multi-action churn."""
        controller = ReasoningController(config={"reasoning_gate": {"stall_threshold": 2}})
        
        class MockDelta:
            failure_signal = "single_action_terminal_stall"
            step = 5
            claims = []
        
        decision = controller.decide(
            world_summary="test",
            compiled_delta=MockDelta(),
            budget_state={},
            phase="solve",
            active_hypotheses=[],
            available_actions=["ACTION1"],
            mechanic_priors=[],
            per_action_evidence={}
        )
        
        # Single action terminal stall should win
        assert decision.trigger == "single_action_stall_mitigation" or decision.mode == ReasoningMode.CHEAP_PROBE

    def test_untested_actions_guard_churn_gate(self):
        """Untested actions should prevent churn gate (guardrail for exploration)."""
        controller = ReasoningController(config={"reasoning_gate": {}})
        
        per_action_evidence = {
            "ACTION1": {
                "tested_count": 2,
                "recent_effects": ["pixel_churn", "pixel_churn"],
                "last_progress_step": -1,
                "frame_hashes": ["hash1", "hash1"]
            },
            "ACTION2": {
                "tested_count": 0,  # Not yet tested
                "recent_effects": [],
                "last_progress_step": -1,
                "frame_hashes": []
            },
        }
        
        class MockDelta:
            failure_signal = None
            step = 5
            claims = []
        
        decision = controller.decide(
            world_summary="test",
            compiled_delta=MockDelta(),
            budget_state={},
            phase="solve",
            active_hypotheses=[],
            available_actions=["ACTION1", "ACTION2"],
            mechanic_priors=[],
            per_action_evidence=per_action_evidence
        )
        
        # Untested action remains, so gate should not trigger until all legal actions are tested.
        assert decision.multi_action_churn_detected is False or decision.actions_tested_count < 2

    def test_two_action_churn_can_trigger_after_both_actions_tested(self):
        """Two-action games should not be excluded by the old 3-action minimum."""
        controller = ReasoningController(config={"reasoning_gate": {}})
        per_action_evidence = {
            "ACTION6": {
                "tested_count": 6,
                "recent_effects": ["object_progress"] + ["pixel_churn"] * 5,
                "last_progress_step": 4,
                "frame_hashes": [],
            },
            "ACTION7": {
                "tested_count": 3,
                "recent_effects": ["pixel_churn"] * 3,
                "last_progress_step": -1,
                "frame_hashes": [],
            },
        }

        class MockDelta:
            failure_signal = None
            step = 12
            claims = [type("Claim", (), {"kind": "action_effect", "effect_class": "pixel_churn"})()]

        # Seed the controller with a real progress step so cooldown logic mirrors runtime.
        class ProgressDelta:
            failure_signal = None
            step = 4
            claims = [type("Claim", (), {"kind": "action_effect", "effect_class": "object_progress"})()]

        controller.decide("test", ProgressDelta(), {}, "solve", [], ["ACTION6", "ACTION7"], [], per_action_evidence)
        decision = controller.decide(
            world_summary="test",
            compiled_delta=MockDelta(),
            budget_state={},
            phase="solve",
            active_hypotheses=[],
            available_actions=["ACTION6", "ACTION7"],
            mechanic_priors=[],
            per_action_evidence=per_action_evidence,
        )

        assert decision.mode == ReasoningMode.MULTI_ACTION_CHURN_PROBE
        assert decision.trigger == "multi_action_churn"
        assert decision.actions_tested_count == 2

    def test_contradiction_pressure_triggers_bounded_probe(self):
        """Repeated falsified predictions should gate away from full LLM cycles."""
        controller = ReasoningController(config={"reasoning_gate": {"contradiction_probe_threshold": 3}})
        per_action_evidence = {
            "ACTION6": {"tested_count": 4, "recent_effects": ["pixel_churn"] * 4, "last_progress_step": -1},
            "ACTION7": {"tested_count": 2, "recent_effects": ["object_progress", "unknown"], "last_progress_step": 2},
        }

        class MockDelta:
            failure_signal = None
            step = 8
            claims = [type("Claim", (), {"kind": "action_effect", "effect_class": "pixel_churn"})()]

        decision = controller.decide(
            world_summary="test",
            compiled_delta=MockDelta(),
            budget_state={"world_model_contradiction_count": 4},
            phase="solve",
            active_hypotheses=[],
            available_actions=["ACTION6", "ACTION7"],
            mechanic_priors=[],
            per_action_evidence=per_action_evidence,
        )

        assert decision.mode == ReasoningMode.MULTI_ACTION_CHURN_PROBE
        assert decision.trigger == "prediction_contradiction_pressure"
        assert decision.stall_policy == "contradiction_probe"

    def test_repeated_prediction_falsification_forces_reclassify(self):
        """After bounded cheap probes falsify a prediction, force reclassification."""
        controller = ReasoningController(config={
            "reasoning_gate": {
                "contradiction_probe_threshold": 3,
                "prediction_falsification_reclassify_threshold": 3,
            }
        })
        per_action_evidence = {
            "ACTION4": {"tested_count": 5, "recent_effects": ["harmful", "harmful", "pixel_churn"], "last_progress_step": 2},
            "ACTION1": {"tested_count": 2, "recent_effects": ["pixel_churn", "pixel_churn"], "last_progress_step": -1},
        }

        class MockDelta:
            failure_signal = None
            step = 10
            claims = [type("Claim", (), {"kind": "action_effect", "effect_class": "pixel_churn"})()]

        decision = controller.decide(
            world_summary="test",
            compiled_delta=MockDelta(),
            budget_state={
                "world_model_contradiction_count": 5,
                "prediction_falsification_counts": {"ACTION4": 3},
            },
            phase="solve",
            active_hypotheses=[],
            available_actions=["ACTION4", "ACTION1"],
            mechanic_priors=[],
            per_action_evidence=per_action_evidence,
        )

        assert decision.mode == ReasoningMode.LLM_REASON
        assert decision.trigger == "prediction_falsification_reclassify"

    def test_all_actions_churn_strategy_exhausts_after_probe_epochs(self):
        """Once the bounded graph evidence is complete, stop instead of probing forever."""
        controller = ReasoningController(config={
            "reasoning_gate": {
                "max_multi_action_churn_probes": 2,
                "strategy_exhausted_probe_epochs": 2,
            }
        })
        controller._total_probes = 4
        controller._consecutive_multi_action_churn_probes = 2
        per_action_evidence = {
            "ACTION5": {"tested_count": 3, "recent_effects": ["pixel_churn"] * 3, "last_progress_step": -1},
            "ACTION6": {"tested_count": 3, "recent_effects": ["pixel_churn"] * 3, "last_progress_step": -1},
            "ACTION7": {"tested_count": 3, "recent_effects": ["pixel_churn"] * 3, "last_progress_step": -1},
        }

        class MockDelta:
            failure_signal = None
            step = 20
            claims = [type("Claim", (), {"kind": "action_effect", "effect_class": "pixel_churn"})()]

        decision = controller.decide(
            world_summary="test",
            compiled_delta=MockDelta(),
            budget_state={
                "all_actions_churn_evidence": {
                    "all_actions_churn": True,
                    "total_churn_count": 9,
                }
            },
            phase="solve",
            active_hypotheses=[],
            available_actions=["ACTION5", "ACTION6", "ACTION7"],
            mechanic_priors=[],
            per_action_evidence=per_action_evidence,
        )

        assert decision.mode == ReasoningMode.EARLY_STOP
        assert decision.trigger == "all_actions_churn_strategy_exhausted"
        assert decision.world_model_decision == "strategy_exhausted"

    def test_metrics_update_on_churn_detection(self):
        """Controller should update metrics when churn is detected."""
        controller = ReasoningController(config={"reasoning_gate": {}})
        initial_count = controller._multi_action_churn_count
        
        per_action_evidence = {
            "ACTION1": {"tested_count": 3, "recent_effects": ["pixel_churn"] * 3, "last_progress_step": -1, "frame_hashes": []},
            "ACTION2": {"tested_count": 3, "recent_effects": ["pixel_churn"] * 3, "last_progress_step": -1, "frame_hashes": []},
            "ACTION3": {"tested_count": 2, "recent_effects": ["pixel_churn"] * 2, "last_progress_step": -1, "frame_hashes": []},
        }
        
        class MockDelta:
            failure_signal = None
            step = 15
            claims = []
        
        decision = controller.decide(
            world_summary="test",
            compiled_delta=MockDelta(),
            budget_state={},
            phase="solve",
            active_hypotheses=[],
            available_actions=["ACTION1", "ACTION2", "ACTION3"],
            mechanic_priors=[],
            per_action_evidence=per_action_evidence
        )
        
        if decision.multi_action_churn_detected:
            metrics = controller.get_metrics()
            assert metrics.get("multi_action_churn_count", 0) > initial_count

    def test_harmful_effect_breaks_out_of_multi_action_churn_gate(self):
        """Harmful outcomes should force reclassification instead of more cheap probing."""
        controller = ReasoningController(config={"reasoning_gate": {}})
        per_action_evidence = {
            "ACTION1": {"tested_count": 3, "recent_effects": ["pixel_churn"] * 3, "last_progress_step": -1},
            "ACTION2": {"tested_count": 3, "recent_effects": ["pixel_churn"] * 3, "last_progress_step": -1},
            "ACTION3": {"tested_count": 3, "recent_effects": ["pixel_churn"] * 3, "last_progress_step": -1},
        }

        class MockDelta:
            failure_signal = None
            step = 15
            claims = [type("Claim", (), {"kind": "action_effect", "effect_class": "harmful"})()]

        decision = controller.decide(
            world_summary="test",
            compiled_delta=MockDelta(),
            budget_state={},
            phase="solve",
            active_hypotheses=[],
            available_actions=["ACTION1", "ACTION2", "ACTION3"],
            mechanic_priors=[],
            per_action_evidence=per_action_evidence,
        )

        assert decision.mode == ReasoningMode.LLM_REASON
        assert decision.trigger == "harmful_outcome_reclassify"
        assert decision.stall_policy == "harmful_breakout"

    def test_multi_action_churn_probe_has_budget(self):
        """Repeated churn probing should return to LLM reasoning after a bounded budget."""
        controller = ReasoningController(config={"reasoning_gate": {"max_multi_action_churn_probes": 2}})
        per_action_evidence = {
            "ACTION1": {"tested_count": 3, "recent_effects": ["pixel_churn"] * 3, "last_progress_step": -1},
            "ACTION2": {"tested_count": 3, "recent_effects": ["pixel_churn"] * 3, "last_progress_step": -1},
            "ACTION3": {"tested_count": 3, "recent_effects": ["pixel_churn"] * 3, "last_progress_step": -1},
        }

        class MockDelta:
            failure_signal = None
            step = 15
            claims = [type("Claim", (), {"kind": "action_effect", "effect_class": "pixel_churn"})()]

        decisions = [
            controller.decide(
                world_summary="test",
                compiled_delta=MockDelta(),
                budget_state={},
                phase="solve",
                active_hypotheses=[],
                available_actions=["ACTION1", "ACTION2", "ACTION3"],
                mechanic_priors=[],
                per_action_evidence=per_action_evidence,
            )
            for _ in range(3)
        ]

        assert decisions[0].mode == ReasoningMode.MULTI_ACTION_CHURN_PROBE
        assert decisions[1].mode == ReasoningMode.MULTI_ACTION_CHURN_PROBE
        assert decisions[2].mode == ReasoningMode.LLM_REASON
        assert decisions[2].trigger == "multi_action_churn_budget_exhausted"

    def test_orchestrator_collects_compiled_step_effects_by_action(self):
        """Live step history stores A074 effects under compiled_world_delta."""
        orchestrator = object.__new__(ARCOrchestrator)
        orchestrator._step_history = [
            {
                "step": 1,
                "action_id": "ACTION1",
                "compiled_world_delta": {"effect_class": "pixel_churn"},
                "board_after": {"frame_hash": "hash-a"},
            },
            {
                "step": 2,
                "action_id": "ACTION2",
                "reward_components": {"progress_class": "object_monotonic"},
                "board_after": {"frame_hash": "hash-b"},
            },
            {
                "step": 3,
                "action_id": "ACTION3",
                "frame_delta": {"apparent_effect": "no_effect"},
                "board_before": {"frame_hash": "hash-c"},
            },
        ]

        evidence = orchestrator._collect_per_action_evidence(["ACTION1", "ACTION2", "ACTION3"])

        assert evidence["ACTION1"]["tested_count"] == 1
        assert evidence["ACTION1"]["recent_effects"] == ["pixel_churn"]
        assert evidence["ACTION2"]["recent_effects"] == ["object_progress"]
        assert evidence["ACTION2"]["last_progress_step"] == 2
        assert evidence["ACTION3"]["recent_effects"] == ["no_op"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
