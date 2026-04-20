"""
Tests for ARC-AGI-3 A/B Harness
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from benchmarks.arc3.harness import ARC3Harness, load_tasks_from_manifest
from benchmarks.ab_harness import ABVariant, ABTask, BenchmarkConfig

@pytest.fixture(autouse=True)
def mock_llm_client():
    with patch("arc_runtime.llm.create_llm_client") as mock:
        mock_client = MagicMock()
        mock_client.chat.return_value = '{"action_id": "ACTION6", "x": 1, "y": 1, "value": 1, "rationale": "mocked"}'
        mock.return_value = mock_client
        yield mock

@pytest.fixture
def mock_db():
    db = MagicMock()
    db.execute_write = AsyncMock()
    no_rows = MagicMock()
    no_rows.has_next.return_value = False
    db.execute.return_value = no_rows
    return db

@pytest.fixture
def arc_config():
    return BenchmarkConfig(
        name="arc3_test",
        parameters={
            "max_attempts_per_puzzle": 5,
            "model": "test-model"
        }
    )

@pytest.fixture
def manifest_path(tmp_path):
    p = tmp_path / "tasks_manifest.json"
    p.write_text("""
    {
      "manifest_version": "1.0",
      "global_seed": 42,
      "tasks": [
        {
          "task_id": "test_001",
          "category": "test",
          "prompt": "Solve test 001",
          "game_id": "game_test_001"
        }
      ]
    }
    """)
    return str(p)

@pytest.mark.asyncio
async def test_arc3_harness_baseline_vs_sidequests(arc_config):
    # Initialize harness in mock mode without a real brain client.
    # Use mock_api=True to skip real API calls and db=None to avoid
    # attempting LocalBrainClient handler initialization (which requires
    # real mcp_engine imports). The harness falls back to NoOpBrainClient
    # for both variants in mock mode.
    harness = ARC3Harness(arc_config, db=None, mock_api=True)

    tasks = [
        ABTask(task_id="t1", category="c1", prompt="p1")
    ]
    setattr(tasks[0], "game_id", "g1")
    harness.create_task_manifest(tasks)

    # Run comparison
    comparison, baseline_meta, sidequests_meta = await harness.run_ab_comparison()
    
    # Verify results exist
    assert baseline_meta.total_tasks == 1
    assert sidequests_meta.total_tasks == 1
    
    # In our mock logic, SIDEQUESTS succeeds in 2 steps, BASELINE in 5 steps
    # Note: steps are 0-indexed in _execute_mock_action call, but incremented after
    # SIDEQUESTS: step 0 (fail), step 1 (success) -> steps = 2
    # BASELINE: step 0, 1, 2, 3 (fail), step 4 (success) -> steps = 5
    
    assert len(harness.baseline_results) == 1
    assert harness.baseline_results[0].steps == 5
    assert harness.baseline_results[0].correct is True
    
    assert len(harness.sidequests_results) == 1
    assert harness.sidequests_results[0].steps == 2
    assert harness.sidequests_results[0].correct is True
    
    # Check metrics
    assert comparison.metrics["steps_to_solve"]["baseline"] == 5.0
    assert comparison.metrics["steps_to_solve"]["sidequests"] == 2.0
    assert comparison.metrics["steps_to_solve"]["delta"] == "-60.0%"

def test_load_tasks_from_manifest(manifest_path):
    tasks = load_tasks_from_manifest(manifest_path)
    assert len(tasks) == 1
    assert tasks[0].task_id == "test_001"
    assert getattr(tasks[0], "game_id") == "game_test_001"

@pytest.mark.asyncio
async def test_deterministic_seed(arc_config):
    # Two runs with same seed should produce same results (in mock mode)
    h1 = ARC3Harness(arc_config, global_seed=42, mock_api=True)
    h2 = ARC3Harness(arc_config, global_seed=42, mock_api=True)
    
    tasks = [ABTask(task_id="t1", category="c1", prompt="p1")]
    setattr(tasks[0], "game_id", "g1")
    
    h1.create_task_manifest(tasks)
    h2.create_task_manifest(tasks)
    
    res1, _ = await h1.run_variant(ABVariant.BASELINE)
    res2, _ = await h2.run_variant(ABVariant.BASELINE)
    
    assert res1[0].steps == res2[0].steps
    assert res1[0].tokens_input == res2[0].tokens_input
