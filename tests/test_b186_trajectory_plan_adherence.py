
import pytest
from benchmarks.arc3.trajectory_eval import TrajectoryEvaluator

def test_plan_adherence_extraction_from_trace():
    evaluator = TrajectoryEvaluator()
    
    # Trace containing a plan registration
    trace = [
        {
            "event_type": "operation",
            "operation": "register_plan",
            "result": {
                "plan_id": "p1",
                "steps": ["ACTION1", "ACTION2"]
            }
        }
    ]
    
    # Step history that doesn't have solve_context.active_chunk.estimated_actions
    step_history = [
        {
            "step": 1,
            "action_id": "ACTION1",
            "solve_context": {}
        },
        {
            "step": 2,
            "action_id": "ACTION2",
            "solve_context": {}
        }
    ]
    
    # Run evaluation
    score = evaluator.evaluate(trace=trace, step_history=step_history)
    
    # Before A012, this would return 10 (neutral) with "no active chunk plans recorded"
    # Now it should successfully find the match
    assert score.plan_adherence > 10
    details = score.details.get("plan_adherence_details", {})
    assert details.get("planned_steps") == 2
    assert details.get("adherence_ratio") == 1.0
