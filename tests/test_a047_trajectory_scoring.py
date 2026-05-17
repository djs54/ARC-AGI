
import pytest
from benchmarks.arc3.trajectory_eval import TrajectoryEvaluator

def test_trajectory_evaluator_handles_empty_input():
    """A047: Verify that evaluate() does not crash on empty input and returns a valid score object."""
    evaluator = TrajectoryEvaluator()
    
    # 1. Totally empty
    score = evaluator.evaluate(trace=[], step_history=[])
    assert score is not None
    assert score.total == 50 # New A047 neutral fallback
    assert score.details["step_count"] == 0 if "step_count" in score.details else True
    
    # 2. None input
    score2 = evaluator.evaluate(trace=None, step_history=None)
    assert score2 is not None
    assert score2.total == 50

def test_trajectory_evaluator_handles_minimal_valid_steps():
    """A047: Verify that evaluate() produces a score for non-zero valid steps."""
    evaluator = TrajectoryEvaluator()
    
    step_history = [
        {
            "step": 1,
            "action_id": "ACTION1",
            "frame_hash": "h1",
            "reward": 0.0,
            "available_actions": ["ACTION1", "ACTION2"]
        }
    ]
    
    score = evaluator.evaluate(step_history=step_history)
    assert score is not None
    assert score.details["step_count"] == 1
    # Should have some non-zero total score
    assert score.total > 0

def test_trajectory_evaluator_extracts_from_trace_if_steps_missing():
    """A055: Verify that evaluate() extracts steps from trace if step_history is missing."""
    evaluator = TrajectoryEvaluator()
    
    trace = [
        {"event_type": "operation", "operation": "step", "details": {"step": 1, "action_id": "A1", "frame_hash": "f1"}}
    ]
    
    score = evaluator.evaluate(trace=trace, step_history=[])
    assert score.details["step_count"] == 1
    assert score.total > 0

def test_trajectory_evaluator_extracts_from_ledger_source():
    """A055: Verify that evaluate() extracts steps from sidequests_ledger in dict payload."""
    evaluator = TrajectoryEvaluator()
    
    payload = {
        "sidequests_ledger": [
            {"step": 1, "action_id": "A1", "kind": "action_adherence", "adherence_ok": True}
        ]
    }
    
    trace, steps = evaluator._split_trace_payload(payload)
    assert len(steps) == 1
    assert steps[0]["action_id"] == "A1"
