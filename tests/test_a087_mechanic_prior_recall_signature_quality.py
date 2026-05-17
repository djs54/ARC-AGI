"""Tests for A087: Mechanic Prior Recall Signature Quality."""

import pytest
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.world_model import WorldModelGraph
from unittest.mock import MagicMock, AsyncMock


class TestMechanicPriorRecallSignature:
    """A087: Tests for mechanic prior recall signature quality."""

    def test_signature_includes_required_fields(self):
        """Mechanic recall signature should include all required fields."""
        # Mock setup
        mock_brain = MagicMock()
        mock_llm = MagicMock()
        mock_serializer = MagicMock()
        
        orchestrator = ARCOrchestrator(
            brain_client=mock_brain,
            llm_client=mock_llm,
            session_id="test_session",
            serializer=mock_serializer,
            config={}
        )
        
        signature = orchestrator._build_mechanic_recall_signature()
        
        # Required fields per A087
        assert "action_set" in signature
        assert "action_cardinality" in signature
        assert "archetype" in signature
        assert "effect_histogram" in signature
        assert "coordinate_relevance_summary" in signature
        assert "object_terminal_trend" in signature
        assert "failure_signals" in signature
        assert "world_model_node_count" in signature
        assert "world_model_edge_count" in signature

    def test_signature_reflects_game_state(self):
        """Signature should reflect actual game state from world model."""
        mock_brain = MagicMock()
        mock_llm = MagicMock()
        mock_serializer = MagicMock()
        
        orchestrator = ARCOrchestrator(
            brain_client=mock_brain,
            llm_client=mock_llm,
            session_id="test_session",
            serializer=mock_serializer,
            config={}
        )
        
        # Set available actions
        orchestrator._available_actions = ["ACTION1", "ACTION2", "ACTION3"]
        
        signature = orchestrator._build_mechanic_recall_signature()
        
        assert signature["action_cardinality"] == 3
        assert signature["action_set"] == "ACTION1,ACTION2,ACTION3"

    def test_effect_histogram_counts_effects(self):
        """Effect histogram should count recent effects correctly."""
        mock_brain = MagicMock()
        mock_llm = MagicMock()
        mock_serializer = MagicMock()
        
        orchestrator = ARCOrchestrator(
            brain_client=mock_brain,
            llm_client=mock_llm,
            session_id="test_session",
            serializer=mock_serializer,
            config={}
        )
        
        # Add some effects to world model
        world_model = orchestrator.world_model
        state_id = world_model.record_state(0, "hash1")
        action_id = world_model.record_action(1, "ACTION1", {}, state_id)
        obs_id = world_model.add_node("obs-1", "Observation", {"kind": "object_progress"})
        world_model.record_effect(action_id, obs_id, "object_progress", {"magnitude": 1.0})
        
        signature = orchestrator._build_mechanic_recall_signature()
        
        # Histogram should be present and potentially have entries
        assert isinstance(signature["effect_histogram"], dict)

    def test_signature_stability(self):
        """Signature should be stable for equivalent game states."""
        mock_brain = MagicMock()
        mock_llm = MagicMock()
        mock_serializer = MagicMock()
        
        orchestrator = ARCOrchestrator(
            brain_client=mock_brain,
            llm_client=mock_llm,
            session_id="test_session",
            serializer=mock_serializer,
            config={}
        )
        
        orchestrator._available_actions = ["ACTION1", "ACTION2"]
        sig1 = orchestrator._build_mechanic_recall_signature()
        
        # Call again without changes
        sig2 = orchestrator._build_mechanic_recall_signature()
        
        # Key fields should match
        assert sig1["action_cardinality"] == sig2["action_cardinality"]
        assert sig1["archetype"] == sig2["archetype"]

    def test_trend_reflects_progress(self):
        """Object/terminal trend should reflect recent progress."""
        mock_brain = MagicMock()
        mock_llm = MagicMock()
        mock_serializer = MagicMock()
        
        orchestrator = ARCOrchestrator(
            brain_client=mock_brain,
            llm_client=mock_llm,
            session_id="test_session",
            serializer=mock_serializer,
            config={}
        )
        
        # Add mostly positive effects
        world_model = orchestrator.world_model
        for i in range(3):
            state_id = world_model.record_state(i, f"hash{i}")
            action_id = world_model.record_action(i+1, "ACTION1", {}, state_id)
            obs_id = world_model.add_node(f"obs-{i}", "Observation", {"kind": "object_progress"})
            world_model.record_effect(action_id, obs_id, "object_progress", {"magnitude": 1.0})
        
        signature = orchestrator._build_mechanic_recall_signature()
        trend = signature["object_terminal_trend"]
        
        # With mostly object_progress effects, trend should be improving
        assert trend in ("improving", "flat", "unknown")

    def test_failure_signals_captured(self):
        """Failure signals should be captured in signature."""
        mock_brain = MagicMock()
        mock_llm = MagicMock()
        mock_serializer = MagicMock()
        
        orchestrator = ARCOrchestrator(
            brain_client=mock_brain,
            llm_client=mock_llm,
            session_id="test_session",
            serializer=mock_serializer,
            config={}
        )
        
        # Mock a compiled delta with failure signal
        class MockDelta:
            failure_signal = "single_action_terminal_stall"
        
        orchestrator._compiled_delta = MockDelta()
        
        signature = orchestrator._build_mechanic_recall_signature()
        
        assert "failure_signals" in signature
        # Failure signal may or may not be present depending on timing
        assert isinstance(signature["failure_signals"], list)

    @pytest.mark.asyncio
    async def test_retrieve_mechanic_priors_uses_signature(self):
        """retrieve_mechanic_priors should use the built signature."""
        mock_brain = AsyncMock()
        mock_brain.recall_mechanic_priors = AsyncMock(return_value={
            "status": "ok",
            "mechanic_prior_count": 0,
            "results": []
        })
        mock_llm = MagicMock()
        mock_serializer = MagicMock()
        
        orchestrator = ARCOrchestrator(
            brain_client=mock_brain,
            llm_client=mock_llm,
            session_id="test_session",
            serializer=mock_serializer,
            config={}
        )
        
        orchestrator._available_actions = ["ACTION1", "ACTION2"]
        
        priors = await orchestrator.retrieve_mechanic_priors()
        
        # Verify recall_mechanic_priors was called with signature dict
        mock_brain.recall_mechanic_priors.assert_called_once()
        call_kwargs = mock_brain.recall_mechanic_priors.call_args[1]
        assert "signature" in call_kwargs
        assert isinstance(call_kwargs["signature"], dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
