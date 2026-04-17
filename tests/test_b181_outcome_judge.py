import pytest
import json
from unittest.mock import MagicMock, AsyncMock
from benchmarks.arc3.outcome_judge import OutcomeJudge, JudgeVerdict

@pytest.fixture
def mock_llm():
    llm = MagicMock()
    # Default successful JSON response
    llm.achat = AsyncMock(return_value='{"structural": 4, "reasoning": 3, "explanation": "Good job."}')
    return llm

@pytest.mark.asyncio
async def test_outcome_judge_full_match(mock_llm):
    judge = OutcomeJudge(mock_llm)
    grid = [[1, 2], [3, 4]]
    
    verdict = await judge.evaluate(
        final_grid=grid,
        expected_grid=grid,
        trajectory_summary="The agent moved things.",
        archetype="space"
    )
    
    assert verdict.partial_match_score == 5
    assert verdict.structural_score == 4
    assert verdict.reasoning_score == 3
    assert verdict.composite_score == round((4 + 5 + 3) / 3.0, 2)

@pytest.mark.asyncio
async def test_outcome_judge_partial_match(mock_llm):
    judge = OutcomeJudge(mock_llm)
    actual = [[1, 2], [3, 0]] # 3/4 match = 75%
    expected = [[1, 2], [3, 4]]
    
    verdict = await judge.evaluate(
        final_grid=actual,
        expected_grid=expected,
        trajectory_summary="Trajectory",
        archetype="race"
    )
    
    # 75% match -> int(0.75 * 5) = int(3.75) = 3
    assert verdict.partial_match_score == 3

@pytest.mark.asyncio
async def test_outcome_judge_zero_match(mock_llm):
    judge = OutcomeJudge(mock_llm)
    actual = [[0, 0], [0, 0]]
    expected = [[1, 2], [3, 4]]
    
    verdict = await judge.evaluate(
        final_grid=actual,
        expected_grid=expected,
        trajectory_summary="Trajectory",
        archetype="race"
    )
    
    assert verdict.partial_match_score == 0

@pytest.mark.asyncio
async def test_outcome_judge_missing_expected():
    judge = OutcomeJudge(MagicMock())
    verdict = await judge.evaluate([[1]], None, "Traj", "race")
    assert verdict is None

@pytest.mark.asyncio
async def test_outcome_judge_llm_failure(mock_llm):
    mock_llm.achat = AsyncMock(side_effect=Exception("Timeout"))
    judge = OutcomeJudge(mock_llm)
    grid = [[1, 2], [3, 4]]
    
    verdict = await judge.evaluate(
        final_grid=grid,
        expected_grid=grid,
        trajectory_summary="Traj",
        archetype="race"
    )
    
    assert verdict is not None
    assert verdict.partial_match_score == 5
    assert verdict.structural_score == 5 # Fallback to partial_match_score
    assert verdict.reasoning_score == 0
    assert "Judge failed" in verdict.explanation

def test_cell_match_logic():
    judge = OutcomeJudge(MagicMock())
    assert judge._compute_cell_match([[1, 1], [1, 1]], [[1, 1], [1, 1]]) == 100.0
    assert judge._compute_cell_match([[1, 1], [1, 1]], [[1, 1], [1, 0]]) == 75.0
    assert judge._compute_cell_match([[1, 1]], [[1, 1, 1]]) == 0.0 # Dimension mismatch
