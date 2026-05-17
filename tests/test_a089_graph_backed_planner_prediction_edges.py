"""Tests for A089: Graph-Backed Planner Prediction Edges."""

import pytest
from agents.arc3.world_model import WorldModelGraph
from agents.arc3.world_model_planner import WorldModelPlanner, PlanMode, PlanSelection
from agents.arc3.orchestrator import ARCOrchestrator
from unittest.mock import MagicMock, AsyncMock
from types import SimpleNamespace


class TestGraphBackedPlannerPredictions:
    """A089: Tests for graph-backed planner prediction edges."""

    def setup_method(self):
        """Set up test fixtures."""
        self.world_model = WorldModelGraph(task_id="test_task", session_id="sess123")
        self.planner = WorldModelPlanner(config={})

    def test_get_action_prediction_evidence_returns_structured_evidence(self):
        """Graph query should return structured prediction evidence with bounded paths."""
        # Record action and effect
        state_id = self.world_model.record_state(step=1, frame_hash="hash1")
        action_id = self.world_model.record_action(step=1, action_id="ACTION1", args={}, state_id=state_id)
        obs_id = self.world_model.add_node("obs-1", "Observation", {"step": 1, "hash": "hash2"})
        self.world_model.record_effect(action_id, obs_id, "object_progress", {"step": 1, "magnitude": 5, "meaningful": True})
        
        # Query prediction evidence
        evidence = self.world_model.get_action_prediction_evidence("ACTION1")
        
        assert evidence["action_id"] == "ACTION1"
        assert "object_progress" in evidence["effect_histogram"]
        assert evidence["effect_histogram"]["object_progress"] == 1
        assert evidence["meaningful_progress_rate"] > 0.0
        assert evidence["confidence"] > 0.0
        assert len(evidence["evidence_path_ids"]) <= 5

    def test_prediction_evidence_empty_for_untested_actions(self):
        """Untested actions should return empty evidence without error."""
        evidence = self.world_model.get_action_prediction_evidence("UNKNOWN_ACTION")
        
        assert evidence["action_id"] == "UNKNOWN_ACTION"
        assert evidence["effect_histogram"] == {}
        assert evidence["meaningful_progress_rate"] == 0.0
        assert evidence["confidence"] == 0.0

    def test_planner_generates_structured_prediction_from_graph(self):
        """Planner should generate structured prediction dict from graph evidence."""
        # Build graph with action evidence
        state_id = self.world_model.record_state(step=1, frame_hash="hash1")
        action_id = self.world_model.record_action(step=1, action_id="ACTION2", args={}, state_id=state_id)
        obs_id = self.world_model.add_node("obs-1", "Observation", {"step": 1, "hash": "hash2"})
        self.world_model.record_effect(action_id, obs_id, "terminal_progress", {"step": 1, "magnitude": 10, "meaningful": True})
        
        # Generate prediction
        prediction = self.planner._generate_prediction_for_action("ACTION2", self.world_model)
        
        assert prediction is not None
        assert isinstance(prediction, dict)
        assert prediction["effect_class"] == "terminal_progress"
        assert prediction["meaningful_progress"] is True
        assert 0.0 < prediction["confidence"] <= 1.0
        assert isinstance(prediction["evidence_path"], list)

    def test_prediction_includes_confidence_and_evidence_path(self):
        """Predictions should include confidence score and bounded evidence path."""
        # Add multiple effects to build histogram
        for i in range(3):
            state_id = self.world_model.record_state(step=i, frame_hash=f"hash{i}")
            action_id = self.world_model.record_action(step=i, action_id="ACTION3", args={}, state_id=state_id)
            obs_id = self.world_model.add_node(f"obs-{i}", "Observation", {"step": i, "hash": f"hash{i+1}"})
            self.world_model.record_effect(action_id, obs_id, "pixel_churn", {"step": i, "magnitude": 1, "meaningful": False})
        
        prediction = self.planner._generate_prediction_for_action("ACTION3", self.world_model)
        
        if prediction:
            assert "confidence" in prediction
            assert "evidence_path" in prediction
            assert len(prediction["evidence_path"]) <= 5

    def test_no_prediction_for_purely_unknown_action_with_no_evidence(self):
        """Actions with no graph evidence should return None prediction."""
        prediction = self.planner._generate_prediction_for_action("NEVER_TESTED", self.world_model)
        assert prediction is None

    def test_planner_candidate_with_prediction_ranks_higher(self):
        """Candidates with predictions should rank higher than probe-only candidates."""
        # Build evidence for ACTION1
        state_id = self.world_model.record_state(step=1, frame_hash="hash1")
        action_id = self.world_model.record_action(step=1, action_id="ACTION1", args={}, state_id=state_id)
        obs_id = self.world_model.add_node("obs-1", "Observation", {"step": 1, "hash": "hash2"})
        self.world_model.record_effect(action_id, obs_id, "object_progress", {"step": 1, "magnitude": 5, "meaningful": True})
        
        # Create planner and select candidate
        plan_selection = self.planner.select_next_candidate(
            world_model=self.world_model,
            mechanic_priors=[],
            available_actions=["ACTION1", "ACTION2"],
            budget_state={}
        )
        
        # Should have candidates
        assert plan_selection.candidate_count > 0
        # Selected should prefer ACTION1 (has evidence)
        assert plan_selection.selected.action_id in ["ACTION1", "ACTION2"]

    def test_bounded_evidence_path_never_exceeds_limit(self):
        """Prediction evidence paths should be capped at 5 IDs."""
        # Add many effects
        for i in range(10):
            state_id = self.world_model.record_state(step=i, frame_hash=f"hash{i}")
            action_id = self.world_model.record_action(step=i, action_id="ACTION_MANY", args={}, state_id=state_id)
            obs_id = self.world_model.add_node(f"obs-{i}", "Observation", {"step": i, "hash": f"obs_hash{i}"})
            self.world_model.record_effect(action_id, obs_id, "pixel_churn", {"step": i, "magnitude": 1, "meaningful": False})
        
        evidence = self.world_model.get_action_prediction_evidence("ACTION_MANY", limit=10)
        
        assert len(evidence["evidence_path_ids"]) <= 5

    def test_world_model_eval_captures_prediction_in_metrics(self):
        """World model eval should extract prediction fields into metrics."""
        from benchmarks.arc3.world_model_eval import WorldModelEvaluator
        
        evaluator = WorldModelEvaluator()
        snapshot = {
            "world_model_node_count": 10,
            "world_model_edge_count": 8,
            "planner_selected_has_prediction": True,
            "selected_prediction": {
                "effect_class": "object_progress",
                "meaningful_progress": True,
                "confidence": 0.75,
                "evidence_path": ["action-1", "effect-2"]
            },
            "planner_selected_has_falsification": True,
            "reasoning_gating": {},
            "compiled_world_delta": {}
        }
        
        metrics = evaluator.build_step_row("test_task", 1, snapshot)
        
        assert metrics.selected_candidate_has_prediction is True
        assert metrics.selected_candidate_prediction_effect_class == "object_progress"
        assert metrics.selected_candidate_prediction_confidence == 0.75

    def test_prediction_miss_records_world_model_contradiction(self):
        """A089 follow-up: selected prediction misses become graph evidence."""
        orchestrator = ARCOrchestrator(
            brain_client=AsyncMock(),
            llm_client=MagicMock(),
            session_id="session-pred",
            serializer=MagicMock(),
            config={"task_id": "test_task"},
        )
        obs_id = orchestrator.world_model.record_observation(1, "hash1", 0.0, 0.0)
        orchestrator._last_planner_selection = SimpleNamespace(
            selected=SimpleNamespace(
                action_id="ACTION1",
                predicted_observation={
                    "effect_class": "pixel_churn",
                    "confidence": 0.7,
                    "evidence_path": ["a1"],
                },
            )
        )
        record = {
            "step": 1,
            "action_id": "ACTION1",
            "compiled_world_delta": {"effect_class": "harmful"},
        }

        orchestrator._record_prediction_feedback(record, obs_id)

        assert orchestrator.world_model.contradiction_count == 1
        assert orchestrator.world_model.demotion_count == 1
        assert record["prediction_mismatch"]["predicted_effect_class"] == "pixel_churn"
        assert record["prediction_mismatch"]["actual_effect_class"] == "harmful"
        assert any(e.get("operation") == "prediction_falsified" for e in orchestrator._execution_trace)

    def test_prediction_feedback_skips_when_selected_action_differs_from_executed(self):
        """Prediction edges should not be attached to the wrong executed action."""
        orchestrator = ARCOrchestrator(
            brain_client=AsyncMock(),
            llm_client=MagicMock(),
            session_id="session-pred-skip",
            serializer=MagicMock(),
            config={"task_id": "test_task"},
        )
        obs_id = orchestrator.world_model.record_observation(1, "hash1", 0.0, 0.0)
        orchestrator._last_planner_selection = SimpleNamespace(
            selected=SimpleNamespace(
                action_id="ACTION4",
                predicted_observation={"effect_class": "object_progress", "confidence": 0.7},
            )
        )
        record = {
            "step": 1,
            "action_id": "ACTION5",
            "compiled_world_delta": {"effect_class": "pixel_churn"},
        }

        orchestrator._record_prediction_feedback(record, obs_id)

        assert "prediction_mismatch" not in record
        assert orchestrator.world_model.contradiction_count == 0
        assert any(e.get("operation") == "prediction_feedback_skipped" for e in orchestrator._execution_trace)

    def test_prediction_falsification_quarantines_repeatedly_wrong_action(self):
        """Repeated falsification should suppress stale graph exploits temporarily."""
        orchestrator = ARCOrchestrator(
            brain_client=AsyncMock(),
            llm_client=MagicMock(),
            session_id="session-pred-quarantine",
            serializer=MagicMock(),
            config={"task_id": "test_task", "reasoning_gate": {"prediction_quarantine_threshold": 2}},
        )
        orchestrator._last_planner_selection = SimpleNamespace(
            selected=SimpleNamespace(
                action_id="ACTION4",
                predicted_observation={"effect_class": "object_progress", "confidence": 0.7},
            )
        )
        for step in [1, 2]:
            obs_id = orchestrator.world_model.record_observation(step, f"hash{step}", 0.0, 0.0)
            orchestrator._record_prediction_feedback(
                {"step": step, "action_id": "ACTION4", "compiled_world_delta": {"effect_class": "pixel_churn"}},
                obs_id,
            )

        assert orchestrator._prediction_falsification_counts["ACTION4"] == 2
        assert orchestrator._prediction_quarantine_until["ACTION4"] > 2

    def test_productive_action_paths_prefer_progress_over_churn(self):
        """Graph traversal should expose actions with causal progress paths."""
        for i in range(3):
            state_id = self.world_model.record_state(step=i + 1, frame_hash=f"hash{i}")
            action_node = self.world_model.record_action(step=i + 1, action_id="ACTION2", args={}, state_id=state_id)
            obs_id = self.world_model.add_node(f"obs-progress-{i}", "Observation", {"step": i + 1, "hash": f"obs{i}"})
            self.world_model.record_effect(action_node, obs_id, "object_progress", {"step": i + 1, "meaningful": True})

        for i in range(4):
            state_id = self.world_model.record_state(step=i + 10, frame_hash=f"churn{i}")
            action_node = self.world_model.record_action(step=i + 10, action_id="ACTION6", args={}, state_id=state_id)
            obs_id = self.world_model.add_node(f"obs-churn-{i}", "Observation", {"step": i + 10, "hash": f"churn_obs{i}"})
            self.world_model.record_effect(action_node, obs_id, "pixel_churn", {"step": i + 10, "meaningful": False})

        paths = self.world_model.get_productive_action_paths(["ACTION2", "ACTION6"])

        assert paths[0]["action_id"] == "ACTION2"
        assert paths[0]["support_count"] == 3
        assert "object_progress" in paths[0]["effect_histogram"]
        assert len(paths[0]["evidence_path_ids"]) <= 5

    def test_planner_selects_graph_backed_productive_exploit(self):
        """Planner should exploit a causal progress path before generic probing."""
        state_id = self.world_model.record_state(step=1, frame_hash="hash1")
        action_node = self.world_model.record_action(step=1, action_id="ACTION2", args={}, state_id=state_id)
        obs_id = self.world_model.add_node("obs-progress", "Observation", {"step": 1, "hash": "obs"})
        self.world_model.record_effect(action_node, obs_id, "object_progress", {"step": 1, "meaningful": True})

        selection = self.planner.select_next_candidate(
            world_model=self.world_model,
            mechanic_priors=[],
            available_actions=["ACTION1", "ACTION2", "ACTION6"],
            budget_state={},
        )

        assert selection.selected.action_id == "ACTION2"
        assert selection.selected.mode == PlanMode.EXPLOIT
        assert selection.selected.evidence_path.startswith("productive_path:ACTION2")
        assert selection.selected.predicted_observation["effect_class"] == "object_progress"

    def test_productive_action_path_decays_after_repeated_churn_misses(self):
        """A single old progress edge should not stay an exploit lock forever."""
        state_id = self.world_model.record_state(step=1, frame_hash="hash-progress")
        action_node = self.world_model.record_action(step=1, action_id="ACTION6", args={}, state_id=state_id)
        obs_id = self.world_model.add_node("obs-progress", "Observation", {"step": 1, "hash": "obs-progress"})
        self.world_model.record_effect(action_node, obs_id, "object_progress", {"step": 1, "meaningful": True})

        for i in range(2, 8):
            state_id = self.world_model.record_state(step=i, frame_hash=f"hash-churn-{i}")
            action_node = self.world_model.record_action(step=i, action_id="ACTION6", args={}, state_id=state_id)
            obs_id = self.world_model.add_node(f"obs-churn-{i}", "Observation", {"step": i, "hash": f"obs-churn-{i}"})
            self.world_model.record_effect(action_node, obs_id, "pixel_churn", {"step": i, "meaningful": False})

        paths = self.world_model.get_productive_action_paths(["ACTION6"], lookback=10)

        assert paths == []

    def test_all_actions_churn_evidence_is_bounded_and_requires_every_action(self):
        """All-actions churn should be a bounded graph conclusion, not a full log scan."""
        actions = ["ACTION5", "ACTION6", "ACTION7"]
        step = 0
        for action_id in actions:
            for _ in range(2):
                step += 1
                state_id = self.world_model.record_state(step=step, frame_hash=f"hash-{step}")
                action_node = self.world_model.record_action(step=step, action_id=action_id, args={}, state_id=state_id)
                obs_id = self.world_model.add_node(f"obs-churn-{step}", "Observation", {"step": step, "hash": f"obs-{step}"})
                self.world_model.record_effect(action_node, obs_id, "pixel_churn", {"step": step, "meaningful": False})

        evidence = self.world_model.get_all_actions_churn_evidence(actions, lookback=18, min_tests_per_action=2)

        assert evidence["all_actions_churn"] is True
        assert evidence["actions_tested_count"] == 3
        assert evidence["required_action_count"] == 3
        assert evidence["total_progress_count"] == 0
        assert len(evidence["evidence_path_ids"]) <= 8

    def test_all_actions_churn_evidence_is_falsified_by_progress(self):
        """A single meaningful progress edge should block the churn failure conclusion."""
        actions = ["ACTION5", "ACTION6"]
        step = 0
        for action_id in actions:
            for idx in range(2):
                step += 1
                state_id = self.world_model.record_state(step=step, frame_hash=f"hash-{step}")
                action_node = self.world_model.record_action(step=step, action_id=action_id, args={}, state_id=state_id)
                obs_id = self.world_model.add_node(f"obs-{step}", "Observation", {"step": step, "hash": f"obs-{step}"})
                effect = "object_progress" if action_id == "ACTION6" and idx == 1 else "pixel_churn"
                self.world_model.record_effect(action_node, obs_id, effect, {"step": step, "meaningful": effect == "object_progress"})

        evidence = self.world_model.get_all_actions_churn_evidence(actions, lookback=18, min_tests_per_action=2)

        assert evidence["all_actions_churn"] is False
        assert evidence["total_progress_count"] == 1
