import pytest
from unittest.mock import MagicMock
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.solver import HybridProgressEvidence

@pytest.fixture
def orchestrator():
    brain = MagicMock()
    llm = MagicMock()
    serializer = MagicMock()
    config = {"macro_executor": {"enabled": True}}
    orch = ARCOrchestrator(
        brain_client=brain,
        llm_client=llm,
        session_id="test-session",
        serializer=serializer,
        config=config
    )
    return orch

def test_sync_autopilot_contract(orchestrator):
    # Setup mock tracker with a result
    evidence = HybridProgressEvidence(
        local_progress=0.8, local_distance=None, local_monotone_steps=3,
        scene_wl_hash="h1", scene_node_count=10,
        graph_text_score=None, graph_text_evidence_count=0, graph_text_top_lesson_ids=[],
        graph_vector_score=None, graph_vector_top_hash=None, graph_vector_top_trajectory_id=None,
        graph_prior_score=None, graph_prior_evidence_count=0,
        combined_similarity=0.8, combined_confidence=0.9, channel_agreement_range=0.0,
        finish_mode_allowed=True, phase="finish", reason="test"
    )
    orchestrator._pattern_tracker.last_result = evidence
    
    # Mock player and goal positions in roles
    orchestrator._solve_context = MagicMock()
    orchestrator._solve_context.object_roles = {
        1: {"role": "player", "confidence": 0.9, "estimated_position": {"row": 0, "col": 0}},
        2: {"role": "goal", "confidence": 0.9, "estimated_position": {"row": 0, "col": 5}}
    }
    
    # Call _try_autopilot SYNC
    obs = {"grid": [[1, 0, 0, 0, 0, 2]]}
    action = orchestrator._try_autopilot(obs, ["ACTION1", "ACTION2", "ACTION3", "ACTION4"])
    
    assert action is not None
    assert action["action_id"] == "ACTION4" # Move right
    assert "autopilot" in action["rationale"]

@pytest.mark.asyncio
async def test_async_enriched_autopilot(orchestrator):
    # Mock tracker update to return an awaitable
    async def mock_update(*args, **kwargs):
        orchestrator._pattern_tracker.last_result = HybridProgressEvidence(
            local_progress=0.8, local_distance=None, local_monotone_steps=3,
            scene_wl_hash="h1", scene_node_count=10,
            graph_text_score=None, graph_text_evidence_count=0, graph_text_top_lesson_ids=[],
            graph_vector_score=None, graph_vector_top_hash=None, graph_vector_top_trajectory_id=None,
            graph_prior_score=None, graph_prior_evidence_count=0,
            combined_similarity=0.8, combined_confidence=0.9, channel_agreement_range=0.0,
            finish_mode_allowed=True, phase="finish", reason="test"
        )
        return orchestrator._pattern_tracker.last_result

    orchestrator._pattern_tracker.update = mock_update
    
    # Mock roles
    orchestrator._solve_context = MagicMock()
    orchestrator._solve_context.object_roles = {
        1: {"role": "player", "confidence": 0.9, "estimated_position": {"row": 0, "col": 0}},
        2: {"role": "goal", "confidence": 0.9, "estimated_position": {"row": 0, "col": 5}}
    }
    
    obs = {"grid": [[1, 0, 0, 0, 0, 2]]}
    action = await orchestrator._try_autopilot_async_enriched(obs, ["ACTION1", "ACTION2", "ACTION3", "ACTION4"])
    
    assert action is not None
    assert action["action_id"] == "ACTION4"
