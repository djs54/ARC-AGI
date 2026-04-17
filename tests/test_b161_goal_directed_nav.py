
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from benchmarks.arc3.state_serializer import StateSerializerForARC
from agents.arc3.orchestrator import ARCOrchestrator, ContentBlock
from agents.arc3.solver import ObjectRole, RoleType

@pytest.fixture
def mock_brain():
    brain = MagicMock()
    brain.notify_turn = AsyncMock(return_value={"status": "ok"})
    brain.register_plan = AsyncMock(return_value={"plan_id": "plan-1"})
    brain.recall_plans = AsyncMock(return_value={"plans": []})
    return brain

@pytest.fixture
def orchestrator(mock_brain):
    return ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=MagicMock(),
        session_id="test-session",
        serializer=StateSerializerForARC(),
        config={},
    )

def test_player_position_tracking(orchestrator):
    # Setup solve context with player color
    orchestrator.solve_engine._object_roles = {
        1: ObjectRole(color_id=1, role=RoleType.PLAYER)
    }
    
    # Observation with player color (1) at (2, 2)
    observation = {
        "grid": [
            [0, 0, 0, 0, 0],
            [0, 0, 0, 0, 0],
            [0, 0, 1, 0, 0],
            [0, 0, 0, 0, 0],
            [0, 0, 0, 0, 0]
        ]
    }
    
    orchestrator._update_player_position(observation)
    assert orchestrator._player_position == (2.0, 2.0)

def test_goal_position_extraction(orchestrator):
    # Setup solve context with goal
    orchestrator.solve_engine._object_roles = {
        2: ObjectRole(
            color_id=2,
            role=RoleType.GOAL,
            estimated_position={"row": 4, "col": 4}
        )
    }
    
    orchestrator._update_goal_position()
    assert orchestrator._goal_position == (4, 4)

def test_directional_guidance_in_prompt(orchestrator):
    orchestrator._player_position = (1.0, 1.0)
    orchestrator._goal_position = (4.0, 4.0)
    
    packet = orchestrator._build_navigation_packet(
        observation={"colors": [], "grid": [[0]]},
        memory_context={},
        step_history=[],
        available_actions=["ACTION1"]
    )
    
    nav_block = packet.get_block("NAVIGATION")
    assert nav_block is not None
    assert "row 1, col 1" in nav_block.content
    assert "row 4, col 4" in nav_block.content
    assert "move down and right" in nav_block.content

def test_movement_history_summary(orchestrator):
    # Add some history with frame deltas
    orchestrator._step_history = [
        {
            "action_id": "ACTION1", # up
            "frame_delta": {"n_cells_changed": 48}
        },
        {
            "action_id": "ACTION3", # left
            "frame_delta": {"n_cells_changed": 0}
        }
    ]
    
    summary = orchestrator._build_movement_summary()
    assert "up: moved (48 pixels changed)" in summary
    assert "left: blocked (wall/no-op)" in summary

def test_action5_effect_logging(orchestrator):
    orchestrator._last_grid = [[0, 0], [0, 0]]
    next_observation = {"grid": [[1, 1], [1, 1]]} # 4 cells changed
    
    # Mocking record_step_result behavior for ACTION5 with >30px change
    # We'll simulate a larger change for the test
    large_grid_before = [[0]*10 for _ in range(10)]
    large_grid_after = [[1]*10 for _ in range(10)] # 100 cells changed
    
    orchestrator._last_grid = large_grid_before
    orchestrator._step_history = [{"action_id": "ACTION5"}]
    
    # We need to mock FrameDelta because it's imported inside record_step_result
    with patch("agents.arc3.grid_analysis.GridDiffEngine") as mock_diff_engine:
        mock_delta = MagicMock()
        mock_delta.n_cells_changed = 100
        mock_delta.new_colors_introduced = [1]
        mock_delta.colors_removed = [0]
        mock_diff_engine.return_value.diff_frames.return_value = mock_delta
        
        orchestrator.record_step_result(0.0, False, {"grid": large_grid_after})
        
    assert orchestrator._last_interact_effect is not None
    assert orchestrator._last_interact_effect["pixels_changed"] == 100
    assert orchestrator._last_interact_effect["new_colors"] == [1]

def test_action5_effect_in_prompt(orchestrator):
    orchestrator._last_interact_effect = {
        "pixels_changed": 50,
        "new_colors": [3],
        "removed_colors": [0],
        "step": 5
    }
    
    packet = orchestrator._build_navigation_packet(
        observation={"colors": [], "grid": [[0]]},
        memory_context={},
        step_history=[],
        available_actions=["ACTION5"]
    )
    
    effects_block = packet.get_block("OBSERVED_EFFECTS")
    assert effects_block is not None
    assert "ACTION5 (interact) caused a major change: 50 pixels" in effects_block.content
    assert "new colors: [3]" in effects_block.content
