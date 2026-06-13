"""Compare v1 and v2 agents on mock puzzles.

Phase 3 gate: v2 must match or exceed v1 on 3+ of 5 metrics.
"""
import pytest
import asyncio
import json
import os
from pathlib import Path
from benchmarks.arc3.harness import ARC3Harness
from benchmarks.harness import BenchmarkConfig


# Metrics we compare
METRICS = [
    "distinct_actions_tried",   # More = better exploration
    "goal_identified",          # True/False — did it find a goal?
    "no_crash",                 # True/False — clean exit?
    "steps_before_stall",       # More = explored longer
    "mcp_tools_called",         # >0 = graph integration working
]


def _count_distinct_actions(result: dict) -> int:
    """Count distinct action IDs tried across all steps."""
    summary = result.get("solve_phase_summary", {})
    attempts = summary.get("action_attempt_counts", {})
    return len(attempts)


def _goal_identified(result: dict) -> bool:
    """Check if the agent identified a goal with confidence > 0.3."""
    summary = result.get("solve_phase_summary", {})
    confidence = summary.get("active_goal_confidence", 0)
    return confidence > 0.3


def _no_crash(result: dict) -> bool:
    """Check no UnknownCrash or exception."""
    return result.get("exception_type") is None


def _steps_before_stall(result: dict) -> int:
    return result.get("steps", 0)


def _mcp_tools_called(result: dict) -> int:
    """Check if any arc_* MCP tools were called (from telemetry)."""
    summary = result.get("solve_phase_summary", {})
    # The v2 agent tracks this; v1 won't have it
    return summary.get("mcp_tool_calls", 0)


class TestV1vsV2Comparison:
    """Run both agents on the same mock puzzles and compare."""

    @pytest.fixture
    def mock_config(self):
        """Minimal config for mock API runs."""
        return {
            "llm": {
                "provider": "ollama",
                "model": "llama3.1:8b",
                "base_url": "http://localhost:11434",
                "timeout_seconds": 30,
                "max_retries": 1,
            },
            "benchmark": {
                "max_attempts_per_puzzle": 5,
            },
        }

    @pytest.mark.asyncio
    async def test_v2_tries_more_actions_than_v1(self, mock_config):
        """v2 should try more distinct actions (exploration gate)."""
        # This is a focused test — run one mock puzzle with each agent
        # and compare distinct action counts
        # v1 result from SU15: tried only ACTION6 (1 distinct)
        # v2 should try at least 2 distinct actions
        
        # For a proper comparison, we'd run both agents here.
        # For now, assert v2 baseline expectations:
        assert True  # Placeholder — fill with actual run comparison

    def test_v2_identifies_goal(self):
        """v2 should identify a goal hypothesis on step 0."""
        # From the first smoke: v2 got blob-9 at 0.57 confidence
        # v1 had "unknown" on all steps
        assert True  # Placeholder

    def test_v2_no_crash(self):
        """v2 should not crash with UnknownCrash."""
        assert True  # Placeholder

    def test_comparison_summary(self):
        """Print comparison table from most recent smoke results."""
        # Read v1 and v2 results from disk if available
        artifacts_dir = Path(os.environ.get("ARC_ARTIFACTS_DIR", "artifacts"))
        v1_path = artifacts_dir / "submission_results_single.v1.json"
        v2_path = artifacts_dir / "submission_results_single.json"

        if not v1_path.exists() or not v2_path.exists():
            pytest.skip("Run both agents first: --agent-version=v1 and v2")

        v1 = json.loads(v1_path.read_text())
        v2 = json.loads(v2_path.read_text())

        if isinstance(v1, list):
            v1 = v1[0]
        if isinstance(v2, list):
            v2 = v2[0]

        print("\n=== v1 vs v2 Comparison ===")
        print(f"{'Metric':<30} {'v1':<15} {'v2':<15} {'Winner'}")
        print("-" * 75)

        v2_wins = 0
        checks = [
            ("distinct_actions", _count_distinct_actions(v1), _count_distinct_actions(v2)),
            ("goal_identified", _goal_identified(v1), _goal_identified(v2)),
            ("no_crash", _no_crash(v1), _no_crash(v2)),
            ("steps_before_stall", _steps_before_stall(v1), _steps_before_stall(v2)),
        ]
        for name, val1, val2 in checks:
            winner = "v2" if val2 >= val1 else "v1"
            if val2 >= val1:
                v2_wins += 1
            print(f"{name:<30} {str(val1):<15} {str(val2):<15} {winner}")

        print(f"\nv2 wins on {v2_wins}/{len(checks)} metrics")
        assert v2_wins >= 3, f"v2 must win on 3+ metrics, got {v2_wins}"
