import pytest
from unittest.mock import MagicMock
from agents.arc3.runner import DurableARCRunner

@pytest.fixture
def runner():
    harness = MagicMock()
    harness.config.parameters = {"max_attempts_per_puzzle": 10}
    brain = MagicMock()
    config = {"max_retries_per_puzzle": 3}
    return DurableARCRunner(harness, brain, config)

def test_compute_progress_reward_meaningful(runner):
    # Case 1: Environment reward
    reward, components = runner._compute_progress_reward(
        env_reward=1.0,
        prev_grid=[[0]], next_grid=[[0]],
        prev_levels_completed=0, next_levels_completed=0,
        prev_score=0, next_score=0,
        target_color_id=None
    )
    assert components["meaningful_progress"] is True
    assert components["progress_class"] == "terminal"

    # Case 2: Level progress
    reward, components = runner._compute_progress_reward(
        env_reward=0.0,
        prev_grid=[[0]], next_grid=[[0]],
        prev_levels_completed=0, next_levels_completed=1,
        prev_score=0, next_score=0,
        target_color_id=None
    )
    assert components["meaningful_progress"] is True
    assert components["progress_class"] == "level"

    # Case 3: Score progress
    reward, components = runner._compute_progress_reward(
        env_reward=0.0,
        prev_grid=[[0]], next_grid=[[0]],
        prev_levels_completed=0, next_levels_completed=0,
        prev_score=0, next_score=10,
        target_color_id=None
    )
    assert components["meaningful_progress"] is True
    assert components["progress_class"] == "score"

    # Case 4: Terminal value score increase
    reward, components = runner._compute_progress_reward(
        env_reward=0.0,
        prev_grid=[[0]], next_grid=[[0]],
        prev_levels_completed=0, next_levels_completed=0,
        prev_score=0, next_score=0,
        target_color_id=None,
        prev_terminal_value_score=5.0,
        next_terminal_value_score=6.0
    )
    assert components["meaningful_progress"] is True
    assert components["progress_class"] == "terminal"

    # Case 5: Target color progress
    reward, components = runner._compute_progress_reward(
        env_reward=0.0,
        prev_grid=[[0]], next_grid=[[1]], # target_color_id = 1
        prev_levels_completed=0, next_levels_completed=0,
        prev_score=0, next_score=0,
        target_color_id=1
    )
    assert components["meaningful_progress"] is True
    assert components["progress_class"] == "object_monotonic"

    # Case 6: Isolated pixel churn (not meaningful)
    reward, components = runner._compute_progress_reward(
        env_reward=0.0,
        prev_grid=[[0,0,0,0,0,0,0,0,0,0]], next_grid=[[1,0,0,0,0,0,0,0,0,0]],
        prev_levels_completed=0, next_levels_completed=0,
        prev_score=0, next_score=0,
        target_color_id=None
    )
    assert components["meaningful_progress"] is False
    assert components["progress_class"] == "pixel_churn"
