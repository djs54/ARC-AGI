
import pytest
import json
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.grid_analysis import GridDiffEngine, CellChange

def test_object_progress_expansion():
    """A063: Verify that player expansion scores positively."""
    engine = GridDiffEngine()
    
    # Player is color 3
    prev_grid = [[3]]
    next_grid = [[3, 3]]
    roles = {3: {"role": "player"}}
    
    progress = engine.compute_object_progress(prev_grid, next_grid, roles)
    assert progress.score > 0
    assert "player_expansion" in progress.components
    assert progress.components["player_expansion"] == 0.2

def test_object_progress_goal_approach():
    """A063: Verify that approaching goal scores positively."""
    engine = GridDiffEngine()
    
    # Player 3, Goal 2
    # Step 1: dist = 2
    prev_grid = [
        [3, 0, 2]
    ]
    # Step 2: dist = 1
    next_grid = [
        [0, 3, 2]
    ]
    roles = {3: {"role": "player"}, 2: {"role": "goal"}}
    
    progress = engine.compute_object_progress(prev_grid, next_grid, roles)
    assert progress.score > 0
    assert "goal_approach" in progress.components
    assert progress.components["goal_approach"] > 0

def test_object_progress_meaningless_toggle():
    """A063: Verify that meaningless toggles (no role match) score zero."""
    engine = GridDiffEngine()
    
    prev_grid = [[0]]
    next_grid = [[1]]
    roles = {3: {"role": "player"}} # Color 1 has no role
    
    progress = engine.compute_object_progress(prev_grid, next_grid, roles)
    assert progress.score == 0
    assert progress.summary == "no structural progress"

@pytest.mark.asyncio
async def test_orchestrator_records_object_progress():
    """A063: Verify that ARCOrchestrator records object progress in step history."""
    brain = AsyncMock()
    orchestrator = ARCOrchestrator(
        brain_client=brain,
        llm_client=MagicMock(),
        session_id="test",
        serializer=MagicMock(),
        config={}
    )
    
    orchestrator._last_grid = [[3]]
    orchestrator._solve_context = {"object_roles": {3: {"role": "player"}}}
    
    # Record a step where player expands
    orchestrator._step_history = [{
        "step": 1,
        "action_id": "ACTION6",
        "board_before": {"frame_hash": "h1"}
    }]
    
    next_observation = {
        "grid": [[3, 3]],
        "state": "NOT_FINISHED"
    }
    
    orchestrator.record_step_result(reward=0.1, done=False, next_observation=next_observation)
    
    record = orchestrator._step_history[-1]
    assert "object_progress" in record
    assert record["object_progress"]["score"] > 0
    assert "player_expansion" in record["object_progress"]["components"]
