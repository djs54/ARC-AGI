import pytest
from unittest.mock import MagicMock
from agents.arc3.runner import DurableARCRunner
from benchmarks.arc3.adapter import NoOpBrainClient
from benchmarks.arc3.trajectory_eval import TrajectoryEvaluator

def test_orchestration_status_suppression_on_small_sample():
    harness = MagicMock()
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})
    
    # 1. Structural violation (phase violation) in a short run
    ledger = [
        {"kind": "current_truth", "phase": "unknown", "step": 1} # Phase violation
    ]
    debug_steps = [
        {"step": 1, "available_actions": ["A1", "A2"]},
        {"step": 2, "available_actions": ["A1", "A2"]}
    ]
    
    report = runner._build_orchestration_report(ledger, progress_log=debug_steps)
    
    assert report["small_sample_size"] is True
    assert report["status"] == "ok"
    assert len(report["suppressed_violations"]) == 1
    assert report["suppressed_violations"][0]["type"] == "phase_violation"

def test_orchestration_status_violation_on_large_sample():
    harness = MagicMock()
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})
    
    # 1. Structural violation in a long run
    ledger = [
        {"kind": "current_truth", "phase": "unknown", "step": 1}
    ]
    debug_steps = [{"step": i} for i in range(10)]
    
    report = runner._build_orchestration_report(ledger, progress_log=debug_steps)
    
    assert report["small_sample_size"] is False
    assert report["status"] == "violation"
    assert len(report["violations"]) == 1

def test_behavioral_violation_triggering():
    harness = MagicMock()
    runner = DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})
    
    # High drift in long run: 4 mismatches in 6 events (>10% rate)
    ledger = [
        {"kind": "action_adherence", "step": i, "adherence_ok": False} for i in range(4)
    ]
    ledger.extend([
        {"kind": "action_adherence", "step": i, "adherence_ok": True} for i in range(4, 6)
    ])
    debug_steps = [{"step": i} for i in range(10)]
    
    report = runner._build_orchestration_report(ledger, progress_log=debug_steps)
    
    assert report["status"] == "violation"
    assert report["planner_executor_adherence"]["mismatches"] == 4
    # Note: no structural violations here, so "violations" list might be empty, 
    # but status should be "violation".
    # Wait, my logic in runner.py: is_violation = bool(violations) or behavioral_violation
    # status = "violation" if is_violation else "ok"

def test_action_diversity_min_score_on_small_sample():
    evaluator = TrajectoryEvaluator()
    
    # Degenerate case: 2 steps, same action, 5 available
    step_history = [
        {"step": 1, "action_id": "A1", "available_actions": ["A1", "A2", "A3", "A4", "A5"]},
        {"step": 2, "action_id": "A1", "available_actions": ["A1", "A2", "A3", "A4", "A5"]},
    ]
    
    score, details = evaluator._score_action_diversity(step_history)
    
    # Normally this would be 0 or very low, but A042 ensures at least 10
    assert score >= 10
    assert details["coverage_ratio"] == 0.2

def test_exploration_efficiency_min_score_on_small_sample():
    evaluator = TrajectoryEvaluator()
    
    # 3 steps, all same frame_hash (zero novel transitions)
    trace = [
        {"details": {"step": 1, "frame_hash": "h1"}, "operation": "step"},
        {"details": {"step": 2, "frame_hash": "h1"}, "operation": "step"},
        {"details": {"step": 3, "frame_hash": "h1"}, "operation": "step"},
    ]
    
    score, details = evaluator._score_exploration_efficiency([], trace=trace)
    
    # Normally this would be 0, but A048 ensures at least 10 for small samples
    assert score >= 10
    assert details["visited_frames"] == 3

def test_plan_adherence_min_score_on_small_sample():
    evaluator = TrajectoryEvaluator()
    
    # 2 steps, both mismatched
    step_history = [
        {"step": 1, "action_id": "A1", "solve_context": {"active_chunk": {"estimated_actions": ["A2"]}}},
        {"step": 2, "action_id": "A1", "solve_context": {"active_chunk": {"estimated_actions": ["A2"]}}},
    ]
    
    score, details = evaluator._score_plan_adherence(step_history)
    assert score >= 10

def test_escalation_quality_min_score_on_small_sample():
    evaluator = TrajectoryEvaluator()
    
    # Very late escalation (step 30) in a 2-step run
    trace = [
        {"event_type": "operation", "operation": "replan", "details": {"step": 30}}
    ]
    step_history = [{"step": 1}, {"step": 2}]
    
    score, details = evaluator._score_escalation_quality(trace, step_history)
    
    # Step 30 usually scores 4, but small sample size should floor it to 10
    assert score >= 10

