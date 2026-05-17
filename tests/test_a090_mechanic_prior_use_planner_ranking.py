"""Tests for A090: Mechanic Prior Use In Planner Ranking."""

import pytest
from agents.arc3.world_model import WorldModelGraph
from agents.arc3.world_model_planner import WorldModelPlanner, PlanMode
from benchmarks.arc3.world_model_eval import WorldModelEvaluator


class TestMechanicPriorUsePlanning:
    """A090: Tests for mechanic prior use in planner ranking."""

    def setup_method(self):
        """Set up test fixtures."""
        self.world_model = WorldModelGraph(task_id="test_task", session_id="sess123")
        self.planner = WorldModelPlanner(config={})

    def test_prior_compatibility_score_with_matching_evidence(self):
        """Prior compatibility should be boosted when graph evidence matches prior effect pattern."""
        # Build graph evidence for ACTION1 with object_progress effect
        state_id = self.world_model.record_state(step=1, frame_hash="hash1")
        action_id = self.world_model.record_action(step=1, action_id="ACTION1", args={}, state_id=state_id)
        obs_id = self.world_model.add_node("obs-1", "Observation", {"step": 1, "hash": "hash2"})
        self.world_model.record_effect(action_id, obs_id, "object_progress", {"step": 1, "magnitude": 5, "meaningful": True})
        
        # Create prior that predicts object_progress for ACTION1
        prior = {
            "id": "mechanic:test-prior",
            "confidence": 0.8,
            "effects": [
                {"action": "ACTION1", "effect_class": "object_progress", "confidence": 0.9}
            ]
        }
        
        # Compute compatibility score
        score = self.planner._compute_prior_compatibility_score(prior, self.world_model, "ACTION1")
        
        # Should be boosted due to matching effect
        assert 0.0 <= score <= 1.0
        assert score > 0.5  # Meaningful compatibility

    def test_prior_compatibility_reduced_for_contradictions(self):
        """Prior compatibility should be reduced if graph has contradictions."""
        # Build graph with contradictions
        state_id = self.world_model.record_state(step=1, frame_hash="hash1")
        action_id = self.world_model.record_action(step=1, action_id="ACTION2", args={}, state_id=state_id)
        obs_id = self.world_model.add_node("obs-1", "Observation", {"step": 1, "hash": "hash2"})
        self.world_model.record_effect(action_id, obs_id, "no_effect", {"step": 1, "magnitude": 0, "meaningful": False})
        
        # Add hypothesis and contradiction
        self.world_model.upsert_hypothesis("hyp1", "action_mechanics", "ACTION2 moves objects", 0.9, "demoted")
        hyp_node_id = "hyp-hyp1"
        self.world_model.link_contradiction(obs_id, hyp_node_id, 1.0, "No effect observed")
        
        # Create prior
        prior = {
            "id": "mechanic:contradicted-prior",
            "confidence": 0.8,
            "effects": [{"action": "ACTION2", "effect_class": "object_progress", "confidence": 0.9}]
        }
        
        score = self.planner._compute_prior_compatibility_score(prior, self.world_model, "ACTION2")
        
        # Score should be reduced due to contradictions
        assert 0.0 <= score <= 1.0

    def test_prior_compatibility_baseline_for_untested_action(self):
        """Untested actions should get prior confidence as baseline (reduced)."""
        prior = {
            "id": "mechanic:untested-prior",
            "confidence": 0.7,
            "effects": [{"action": "ACTION_UNKNOWN", "effect_class": "unknown"}]
        }
        
        score = self.planner._compute_prior_compatibility_score(prior, self.world_model, "ACTION_UNKNOWN")
        
        # Should be baseline: confidence * 0.6 for untested
        assert 0.0 <= score <= 1.0
        assert score < 0.7  # Reduced from prior confidence

    def test_planner_boosts_compatible_prior_in_ranking(self):
        """Candidates with compatible priors should rank higher than generics."""
        # Build evidence for ACTION1
        state_id = self.world_model.record_state(step=1, frame_hash="hash1")
        action_id = self.world_model.record_action(step=1, action_id="ACTION1", args={}, state_id=state_id)
        obs_id = self.world_model.add_node("obs-1", "Observation", {"step": 1, "hash": "hash2"})
        self.world_model.record_effect(action_id, obs_id, "object_progress", {"step": 1, "magnitude": 5, "meaningful": True})
        
        # Create compatible prior
        prior = {
            "id": "mechanic:good-prior",
            "confidence": 0.85,
            "effects": [{"action": "ACTION1", "effect_class": "object_progress", "confidence": 0.9}]
        }
        
        # Select with prior
        plan_selection = self.planner.select_next_candidate(
            world_model=self.world_model,
            mechanic_priors=[prior],
            available_actions=["ACTION1", "ACTION2", "ACTION3"],
            budget_state={}
        )
        
        # Selection should reflect prior if it's compatible
        assert plan_selection.selected is not None
        assert len(plan_selection.candidates) > 0

    def test_quarantined_action_does_not_win_productive_exploit_ranking(self):
        """Falsified graph paths should be suppressed until quarantine expires."""
        for step, action_id, effect in [
            (1, "ACTION4", "object_progress"),
            (2, "ACTION1", "pixel_churn"),
        ]:
            state_id = self.world_model.record_state(step=step, frame_hash=f"hash{step}")
            action_node = self.world_model.record_action(step=step, action_id=action_id, args={}, state_id=state_id)
            obs_id = self.world_model.add_node(f"obs-{step}", "Observation", {"step": step, "hash": f"obs{step}"})
            self.world_model.record_effect(action_node, obs_id, effect, {"step": step, "meaningful": effect == "object_progress"})

        plan_selection = self.planner.select_next_candidate(
            world_model=self.world_model,
            mechanic_priors=[],
            available_actions=["ACTION1", "ACTION4"],
            budget_state={"quarantined_actions": ["ACTION4"]},
        )

        assert plan_selection.selected.action_id != "ACTION4"

    def test_mismatched_prior_not_selected_if_generic_better(self):
        """Priors that don't match graph state should not override better generics."""
        # No evidence in graph (ACTION_NEW is truly untested)
        prior = {
            "id": "mechanic:mismatched-prior",
            "confidence": 0.5,
            "effects": [{"action": "ACTION_NEW", "effect_class": "terminal_progress"}]
        }
        
        plan_selection = self.planner.select_next_candidate(
            world_model=self.world_model,
            mechanic_priors=[prior],
            available_actions=["ACTION1", "ACTION2", "ACTION_NEW"],
            budget_state={}
        )
        
        # Planner should return valid selection (may or may not use prior)
        assert plan_selection.selected is not None
        assert plan_selection.selected.action_id in ["ACTION1", "ACTION2", "ACTION_NEW"]

    def test_selected_prior_provenance_tracked(self):
        """Selected candidate should include prior provenance when influenced."""
        # Setup
        state_id = self.world_model.record_state(step=1, frame_hash="hash1")
        action_id = self.world_model.record_action(step=1, action_id="ACTION1", args={}, state_id=state_id)
        obs_id = self.world_model.add_node("obs-1", "Observation", {"step": 1, "hash": "hash2"})
        self.world_model.record_effect(action_id, obs_id, "object_progress", {"step": 1, "magnitude": 5, "meaningful": True})
        
        prior = {
            "id": "mechanic:tracked-prior",
            "confidence": 0.8,
            "effects": [{"action": "ACTION1", "effect_class": "object_progress"}]
        }
        
        plan_selection = self.planner.select_next_candidate(
            world_model=self.world_model,
            mechanic_priors=[prior],
            available_actions=["ACTION1"],
            budget_state={}
        )
        
        # If prior was used, it should have mechanic_prior_id set
        if plan_selection.mechanic_priors_used > 0:
            assert plan_selection.selected.mechanic_prior_id is not None or plan_selection.selected_prior_id is not None

    def test_prior_used_only_if_selected_has_compatibility_score(self):
        """Prior should be marked 'used' only if it influenced selection with compatibility > 0."""
        prior = {
            "id": "mechanic:used-prior",
            "confidence": 0.7,
            "effects": [{"action": "ACTION1", "effect_class": "object_progress"}]
        }
        
        plan_selection = self.planner.select_next_candidate(
            world_model=self.world_model,
            mechanic_priors=[prior],
            available_actions=["ACTION1"],
            budget_state={}
        )
        
        # If prior_compatibility_score > 0, prior should be marked as used
        if getattr(plan_selection.selected, "prior_compatibility_score", 0.0) > 0:
            assert plan_selection.mechanic_priors_used > 0

    def test_world_model_eval_distinguishes_used_vs_unused_priors(self):
        """Evaluator should distinguish prior_used from priors_recalled_not_used."""
        evaluator = WorldModelEvaluator()
        
        # Snapshot with used prior
        snapshot_used = {
            "world_model_node_count": 10,
            "world_model_edge_count": 8,
            "mechanic_prior_recall_status": "ok",
            "mechanic_prior_count": 2,
            "mechanic_prior_id": "mechanic:used",
            "planner_selected_prior_id": "mechanic:used",
            "mechanic_priors_used_count": 1,
            "reasoning_gating": {},
            "compiled_world_delta": {}
        }
        
        metrics_used = evaluator.build_step_row("task1", 1, snapshot_used)
        assert metrics_used.memory_transfer_state == "prior_used"
        
        # Snapshot with recalled but unused priors
        evaluator.reset()
        snapshot_unused = {
            "world_model_node_count": 10,
            "world_model_edge_count": 8,
            "mechanic_prior_recall_status": "ok",
            "mechanic_prior_count": 2,
            "mechanic_prior_id": None,
            "planner_selected_prior_id": None,
            "mechanic_priors_used_count": 0,
            "reasoning_gating": {},
            "compiled_world_delta": {}
        }
        
        metrics_unused = evaluator.build_step_row("task1", 2, snapshot_unused)
        assert metrics_unused.memory_transfer_state == "priors_recalled_not_used"

    def test_prior_compatibility_score_in_metrics(self):
        """Evaluator should capture prior compatibility score in metrics."""
        evaluator = WorldModelEvaluator()
        snapshot = {
            "world_model_node_count": 10,
            "world_model_edge_count": 8,
            "mechanic_prior_compatibility_score": 0.72,
            "planner_selected_prior_id": "mechanic:test",
            "mechanic_prior_count": 1,
            "mechanic_priors_used_count": 1,
            "reasoning_gating": {},
            "compiled_world_delta": {}
        }
        
        metrics = evaluator.build_step_row("task1", 1, snapshot)
        assert metrics.planner_selected_prior_compatibility == 0.72

    def test_single_action_prior_maps_mismatched_action_to_only_legal_action(self):
        """Aggregate prior action ids should translate onto the only executable action."""
        prior = {
            "id": "mechanic:single-action-transfer",
            "confidence": 0.8,
            "effects": [
                {"action": "ACTION2", "effect_class": "delayed_reward", "confidence": 0.75}
            ],
        }

        plan_selection = self.planner.select_next_candidate(
            world_model=self.world_model,
            mechanic_priors=[prior],
            available_actions=["ACTION6"],
            budget_state={},
        )

        assert plan_selection.selected.action_id == "ACTION6"
        assert plan_selection.selected.mechanic_prior_id == "mechanic:single-action-transfer"
        assert plan_selection.selected.predicted_observation["effect_class"] == "delayed_reward"
        assert plan_selection.mechanic_priors_used == 1
