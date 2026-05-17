
import pytest
import json
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.grid_analysis import CellChange

@pytest.mark.asyncio
async def test_coordinate_relevance_detection_true():
    """A062: Verify that tracking click-like actions marks them as effective."""
    brain = AsyncMock()
    config = {}
    orchestrator = ARCOrchestrator(
        brain_client=brain,
        llm_client=MagicMock(),
        session_id="test",
        serializer=MagicMock(),
        config=config
    )
    
    action_id = "ACTION6"
    # Sample 1: click (1,1) -> cell (1,1) changed
    orchestrator._update_coordinate_relevance(action_id, (1, 1), [CellChange(1, 1, 0, 1)])
    # Sample 2: click (2,2) -> cell (2,2) changed
    orchestrator._update_coordinate_relevance(action_id, (2, 2), [CellChange(2, 2, 0, 1)])
    # Sample 3: click (3,3) -> cell (3,3) changed
    orchestrator._update_coordinate_relevance(action_id, (3, 3), [CellChange(3, 3, 0, 1)])
    
    assert orchestrator.get_args_effective(action_id) is True
    status = orchestrator._action_coord_relevance[action_id]
    assert status["args_effective"] == "true"

@pytest.mark.asyncio
async def test_coordinate_relevance_detection_false():
    """A062: Verify that varied requests with fixed effects marks them as ineffective."""
    brain = AsyncMock()
    config = {}
    orchestrator = ARCOrchestrator(
        brain_client=brain,
        llm_client=MagicMock(),
        session_id="test",
        serializer=MagicMock(),
        config=config
    )
    
    action_id = "ACTION6"
    # Sample 1: click (10,10) -> cell (1,61) changed (far away)
    orchestrator._update_coordinate_relevance(action_id, (10, 10), [CellChange(1, 61, 0, 1)])
    # Sample 2: click (20,20) -> cell (1,60) changed (still far from request)
    orchestrator._update_coordinate_relevance(action_id, (20, 20), [CellChange(1, 60, 0, 1)])
    # Sample 3: click (30,30) -> cell (1,59) changed (still far)
    orchestrator._update_coordinate_relevance(action_id, (30, 30), [CellChange(1, 59, 0, 1)])
    
    assert orchestrator.get_args_effective(action_id) is False
    status = orchestrator._action_coord_relevance[action_id]
    assert status["args_effective"] == "false"

@pytest.mark.asyncio
async def test_ensure_action6_irrelevant_default():
    """A062: Verify that irrelevant coordinates are defaulted to (0,0)."""
    brain = AsyncMock()
    config = {}
    orchestrator = ARCOrchestrator(
        brain_client=brain,
        llm_client=MagicMock(),
        session_id="test",
        serializer=MagicMock(),
        config=config
    )
    
    action_id = "ACTION6"
    # Force ineffective state
    orchestrator._action_coord_relevance[action_id] = {
        "args_effective": "false",
        "samples": []
    }
    
    action = {"action_id": "ACTION6", "x": 10, "y": 10, "rationale": "probing"}
    observation = {"grid": [[0]]}
    
    updated = orchestrator._ensure_action6_coordinates(action, observation)
    assert updated["x"] == 0
    assert updated["y"] == 0
    assert "coordinate_irrelevant_default" in updated["rationale"]

@pytest.mark.asyncio
async def test_build_action_packet_includes_warning():
    """A062: Verify that prompt includes warning for irrelevant actions."""
    brain = AsyncMock()
    config = {}
    orchestrator = ARCOrchestrator(
        brain_client=brain,
        llm_client=MagicMock(),
        session_id="test",
        serializer=MagicMock(),
        config=config
    )
    
    orchestrator._action_coord_relevance["ACTION6"] = {
        "args_effective": "false",
        "samples": []
    }
    
    observation = {"grid": [[0]], "colors": [], "shapes": []}
    memory_context = {}
    available_actions = ["ACTION6", "ACTION1"]
    
    orchestrator.serializer._estimate_tokens.return_value = 100
    
    packet = orchestrator.build_action_packet(observation, memory_context, [], available_actions)
    
    # Find ACTION_SEMANTICS block
    block = packet.get_block("ACTION_SEMANTICS")
    assert block is not None
    assert "ACTION6" in block.content
    assert "ignores the requested x/y coordinates" in block.content
