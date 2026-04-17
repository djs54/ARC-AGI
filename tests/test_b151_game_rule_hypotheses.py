
import pytest
import json
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.solver import GameRuleHypothesizer, GameRuleHypothesis
from agents.arc3.grid_analysis import LevelPattern

@pytest.mark.asyncio
async def test_game_rule_hypothesizer_fast_path():
    """B151: Verify fast path generation when pattern confidence is high."""
    hypothesizer = GameRuleHypothesizer()
    
    level_pattern = LevelPattern(
        consistent_action_effects={"ACTION1": "move up"},
        consistent_color_map={1: 2},
        consistent_spatial_pattern="recolor",
        game_rule_summary="Recolor rule",
        confidence=0.95,
        n_levels=2
    )
    
    # Should not call LLM
    llm = MagicMock()
    
    hypotheses = await hypothesizer.hypothesize(level_pattern, [], llm)
    
    assert len(hypotheses) >= 1
    h = hypotheses[0]
    assert h.source == "level_analysis"
    assert h.confidence == 0.95
    assert h.action_semantics["ACTION1"] == "move up"

@pytest.mark.asyncio
async def test_game_rule_hypothesizer_llm_path():
    """B151: Verify LLM path generation when confidence is lower."""
    llm = MagicMock()
    mock_resp = MagicMock()
    mock_resp.content = json.dumps({
        "rule_description": "Move red to blue",
        "action_semantics": {"ACTION1": "up"},
        "objective_description": "win",
        "level_strategy": "go",
        "confidence": 0.8
    })
    llm.chat = AsyncMock(return_value=mock_resp)
    
    hypothesizer = GameRuleHypothesizer()
    
    level_pattern = LevelPattern(
        consistent_action_effects={},
        consistent_color_map={},
        consistent_spatial_pattern=None,
        game_rule_summary="Mixed pattern",
        confidence=0.5,
        n_levels=1
    )
    
    solved_levels = [{
        "level": 1,
        "actions": ["ACTION1"],
        "steps": 1,
        "start_grid": [[0]],
        "end_grid": [[1]],
        "win_levels": 8
    }]
    
    hypotheses = await hypothesizer.hypothesize(level_pattern, solved_levels, llm)
    
    assert len(hypotheses) >= 1
    h = hypotheses[0]
    assert h.source == "llm"
    assert h.rule_description == "Move red to blue"
    assert h.confidence == 0.8
