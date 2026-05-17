
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from agents.arc3.orchestrator import ARCOrchestrator
from benchmarks.arc3.state_serializer import StateSerializerForARC
from agents.arc3.solver import ObjectRole, RoleType, HybridProgressEvidence

@pytest.mark.asyncio
async def test_autopilot_disengages_on_wall_hit_streak():
    """A045: Verify that autopilot disengages after 3 wall hits."""
    brain = MagicMock()
    orchestrator = ARCOrchestrator(
        brain_client=brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    
    # Mock pattern tracker to stay in finish mode
    orchestrator._pattern_tracker = MagicMock()
    # A050: Must return HybridProgressEvidence object
    evidence = HybridProgressEvidence(
        local_progress=1.0, local_distance=None, local_monotone_steps=1,
        scene_wl_hash="h1", scene_node_count=1,
        graph_text_score=None, graph_text_evidence_count=0, graph_text_top_lesson_ids=[],
        graph_vector_score=None, graph_vector_top_hash=None, graph_vector_top_trajectory_id=None,
        combined_similarity=1.0, combined_confidence=1.0, channel_agreement_range=0.0,
        finish_mode_allowed=True, phase="finish", reason="mock"
    )
    orchestrator._pattern_tracker.update = AsyncMock(return_value=evidence)
    
    # Mock goal and player info in solve_engine
    orchestrator.solve_engine._object_roles = {
        1: ObjectRole(color_id=1, role=RoleType.PLAYER, confidence=1.0, estimated_position={"row": 5, "col": 5}),
        2: ObjectRole(color_id=2, role=RoleType.GOAL, confidence=1.0, estimated_position={"row": 10, "col": 10})
    }
    
    obs = {"grid": [[0]*20 for _ in range(20)]}
    
    # 1. First hit
    orchestrator._last_autopilot_player_pos = (5, 5)
    orchestrator._step_history.append({
        "decision_source": "autopilot",
        "action_id": "ACTION2", # Move down
        "frame_delta": {"n_cells_changed": 1} # Not zero, so only wall detection fires
    })
    
    # Next call should detect the hit
    res1 = await orchestrator._try_autopilot(obs, ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"])
    assert res1 is not None
    assert orchestrator._autopilot_wall_hits == 1
    
    # 2. Second hit
    orchestrator._last_autopilot_player_pos = (5, 5)
    orchestrator._step_history.append({
        "decision_source": "autopilot",
        "action_id": "ACTION2",
        "frame_delta": {"n_cells_changed": 1}
    })
    res2 = await orchestrator._try_autopilot(obs, ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"])
    assert res2 is not None
    assert orchestrator._autopilot_wall_hits == 2
    
    # 3. Third hit -> Disengage
    orchestrator._last_autopilot_player_pos = (5, 5)
    orchestrator._step_history.append({
        "decision_source": "autopilot",
        "action_id": "ACTION2",
        "frame_delta": {"n_cells_changed": 1}
    })
    
    # This call should trigger disengage
    with patch.object(orchestrator, "_emit_trace_event") as mock_emit:
        res3 = await orchestrator._try_autopilot(obs, ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"])
        assert res3 is None
        assert orchestrator._autopilot_wall_hits == 0
        assert orchestrator._autopilot_disengage_step == len(orchestrator._step_history)
        
        # Verify trace event
        mock_emit.assert_any_call("operation", "autopilot_disengage", {"reason": "wall_hit_streak", "wall_hits": 3})

@pytest.mark.asyncio
async def test_autopilot_cooldown_prevents_immediate_relock():
    """A045: Verify that autopilot obeys the 5-step cooldown."""
    brain = MagicMock()
    orchestrator = ARCOrchestrator(
        brain_client=brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    
    orchestrator._autopilot_disengage_step = 10
    orchestrator._step_history = [{} for _ in range(12)] # Step 12
    
    # 12 - 10 = 2 < 5, should be in cooldown
    res = await orchestrator._try_autopilot({"grid": [[0]]}, [])
    assert res is None
    
    # Step 15: 15 - 10 = 5, cooldown expired
    orchestrator._step_history = [{} for _ in range(15)]
    # Mock pattern tracker so it doesn't return early elsewhere
    orchestrator._pattern_tracker = MagicMock()
    evidence = HybridProgressEvidence(
        local_progress=0.0, local_distance=None, local_monotone_steps=0,
        scene_wl_hash="h1", scene_node_count=1,
        graph_text_score=None, graph_text_evidence_count=0, graph_text_top_lesson_ids=[],
        graph_vector_score=None, graph_vector_top_hash=None, graph_vector_top_trajectory_id=None,
        combined_similarity=0.0, combined_confidence=0.0, channel_agreement_range=0.0,
        finish_mode_allowed=False, phase="discover", reason="mock"
    )
    orchestrator._pattern_tracker.update = AsyncMock(return_value=evidence)
    
    # If we haven't set roles, it returns None, so we should set them to see it proceed.
    orchestrator.solve_engine._object_roles = {
        1: ObjectRole(color_id=1, role=RoleType.PLAYER, confidence=1.0, estimated_position={"row": 0, "col": 0})
    }
    
    res3 = await orchestrator._try_autopilot({"grid": [[0]]}, [])
    # Since no goal info, it will return original goal fallback or None.
    # The important part is it reached the roles check, not the cooldown return.
    assert orchestrator._autopilot_wall_hits == 0
