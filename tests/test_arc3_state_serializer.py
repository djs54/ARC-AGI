"""Contract tests for ARC state serializer round-trip accuracy and token efficiency."""

from __future__ import annotations

import pytest

from benchmarks.arc3.state_serializer import StateSerializerForARC


def test_single_cell_delta_and_narrative() -> None:
    """Verify single-cell change is captured in both machine and narrative forms."""
    serializer = StateSerializerForARC()
    
    before = [[1, 1], [0, 0]]
    after = [[1, 1], [0, 2]]
    action = {"action_type": "PAINT", "rationale": "paint"}
    
    result = serializer.serialize_transition(
        before, after, action, reward=0.5, done=False
    )
    
    assert result["is_valid"] is True
    assert result["machine_delta"]["num_changes"] == 1
    assert result["machine_delta"]["changes"][0] == {"coords": [1, 1], "before": 0, "after": 2}
    assert "PAINT" in result["narrative"]
    assert "reward=0.50" in result["narrative"]
    assert "0→2" in result["narrative"]


def test_multi_cell_fill() -> None:
    """Verify multi-cell change is correctly recorded."""
    serializer = StateSerializerForARC()
    
    before = [[1, 1, 1], [0, 0, 0], [0, 0, 0]]
    after = [[1, 1, 1], [3, 3, 3], [0, 0, 0]]
    action = {"action_type": "FILL", "rationale": "fill row"}
    
    result = serializer.serialize_transition(
        before, after, action, reward=1.0, done=True
    )
    
    assert result["is_valid"] is True
    assert result["machine_delta"]["num_changes"] == 3
    assert "FILL" in result["narrative"]
    assert "done" in result["narrative"]
    assert "reward=1.00" in result["narrative"]
    # Verify all three changes are in the machine form
    coords_set = {tuple(c["coords"]) for c in result["machine_delta"]["changes"]}
    assert coords_set == {(1, 0), (1, 1), (1, 2)}


def test_no_changes() -> None:
    """Verify that identical before/after yields no_changes."""
    serializer = StateSerializerForARC()
    
    state = [[1, 2], [3, 4]]
    action = {"action_type": "NOP", "rationale": "no-op"}
    
    result = serializer.serialize_transition(
        state, state, action, reward=0.0, done=False
    )
    
    assert result["is_valid"] is True
    assert result["machine_delta"]["num_changes"] == 0
    assert "no changes" in result["narrative"]


def test_round_trip_accuracy() -> None:
    """Verify reconstruction from delta achieves 100% fidelity."""
    serializer = StateSerializerForARC()
    
    before = [[1, 1, 0], [0, 2, 2], [3, 3, 0]]
    after = [[1, 1, 9], [0, 2, 2], [3, 3, 0]]
    action = {"action_type": "PAINT", "rationale": "test"}
    
    result = serializer.serialize_transition(before, after, action)
    
    # Reconstruct using the delta
    reconstructed = serializer._reconstruct_state(before, result["machine_delta"])
    
    # Verify exact match
    assert reconstructed == after
    assert serializer.get_fidelity_score() == 100.0


def test_token_budget_estimation() -> None:
    """Verify token estimation stays within configured bounds."""
    serializer = StateSerializerForARC(max_tokens_per_step=256)
    
    # Simple small change
    before = [[0] * 10 for _ in range(10)]
    after = [[0] * 10 for _ in range(10)]
    after[0][0] = 5
    
    action = {"action_type": "PAINT", "rationale": "test"}
    result = serializer.serialize_transition(before, after, action)
    
    # Token usage should be well below max
    assert result["tokens_used"] <= serializer.max_tokens_per_step
    assert result["tokens_used"] > 0


def test_multiple_steps_logging() -> None:
    """Verify step logs accumulate correctly."""
    serializer = StateSerializerForARC()
    
    action = {"action_type": "PAINT", "rationale": "test"}
    
    # Step 1
    serializer.serialize_transition(
        [[0, 0], [0, 0]],
        [[1, 0], [0, 0]],
        action,
        reward=0.5
    )
    
    # Step 2
    serializer.serialize_transition(
        [[1, 0], [0, 0]],
        [[1, 2], [0, 0]],
        action,
        reward=1.0,
        done=True
    )
    
    logs = serializer.get_step_logs()
    assert len(logs) == 2
    assert logs[0]["reward"] == 0.5
    assert logs[0]["done"] is False
    assert logs[1]["reward"] == 1.0
    assert logs[1]["done"] is True


def test_grid_size_change() -> None:
    """Verify serializer handles grid expansion gracefully."""
    serializer = StateSerializerForARC()
    
    # Grid expands from 2x2 to 3x3
    before = [[1, 1], [1, 1]]
    after = [[1, 1, 0], [1, 1, 0], [0, 0, 0]]
    
    action = {"action_type": "EXPAND", "rationale": "expand"}
    result = serializer.serialize_transition(before, after, action)
    
    assert result["is_valid"] is True
    assert result["machine_delta"]["grid_shape_before"] == [2, 2]
    assert result["machine_delta"]["grid_shape_after"] == [3, 3]


def test_token_statistics() -> None:
    """Verify token statistics accumulation."""
    serializer = StateSerializerForARC()
    
    action = {"action_type": "PAINT", "rationale": "test"}
    
    # Three steps with varying complexity
    serializer.serialize_transition(
        [[0, 0], [0, 0]],
        [[1, 0], [0, 0]],
        action,
        reward=0.5
    )
    
    serializer.serialize_transition(
        [[1, 0, 0], [0, 0, 0], [0, 0, 0]],
        [[1, 2, 3], [0, 0, 0], [0, 0, 0]],
        action,
        reward=1.0
    )
    
    stats = serializer.get_token_statistics()
    assert stats["total"] > 0
    assert stats["avg"] > 0
    assert stats["max"] >= stats["avg"]
    assert stats["max"] <= stats["total"]


def test_fidelity_score_with_mixed_results() -> None:
    """Verify fidelity score calculation."""
    serializer = StateSerializerForARC()
    
    action = {"action_type": "PAINT", "rationale": "test"}
    
    # Two valid transitions
    serializer.serialize_transition(
        [[0, 0], [0, 0]],
        [[1, 0], [0, 0]],
        action
    )
    
    serializer.serialize_transition(
        [[1, 0], [0, 0]],
        [[1, 2], [0, 0]],
        action
    )
    
    # Both should be valid, so fidelity = 100%
    assert serializer.get_fidelity_score() == 100.0
