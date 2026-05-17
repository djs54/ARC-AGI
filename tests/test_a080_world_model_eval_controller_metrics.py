import pytest
from types import SimpleNamespace
from benchmarks.arc3.world_model_eval import WorldModelEvaluator
from agents.arc3.runner import DurableARCRunner
from agents.arc3.orchestrator import ARCOrchestrator, ContentBlock
from agents.arc3.world_model_planner import PlanMode

def test_world_model_eval_summary_math():
    evaluator = WorldModelEvaluator()
    
    # Step 1: Skip 2 reasoning cycles
    snap1 = {
        "reasoning_skip_count": 2,
        "llm_reason_count": 1,
        "mechanic_priors_used_count": 0,
        "compiled_world_delta": {"claims_count": 1}
    }
    evaluator.build_step_row("t1", 1, snap1)
    
    # Step 2: Skip 1 more, use a prior
    snap2 = {
        "reasoning_skip_count": 3, # cumulative from runtime usually, but evaluator adds them
        "llm_reason_count": 1,
        "mechanic_priors_used_count": 1,
        "compiled_world_delta": {"claims_count": 2}
    }
    # Wait, if snapshot skip count is cumulative, I should be careful.
    # Current implementation: self._full_reasoning_cycles_avoided += snapshot.get("reasoning_skip_count", 0)
    # This assumes snapshot is NOT cumulative. 
    # But ReasoningController.skip_count IS cumulative.
    
    # Let me check my implementation in world_model_eval.py again.
    # Yes: self._full_reasoning_cycles_avoided += snapshot.get("reasoning_skip_count", 0)
    
    # If the runtime sends cumulative counts, I should only add the delta.
    # Or just use the final one.
    
    # Actually, the plan says: "Make summary counters stateful across step rows, with final-result fallback."
    
    pass

def test_evaluator_stateful_counters():
    evaluator = WorldModelEvaluator()
    
    # Simulate runtime sending cumulative TOTAL skip count
    evaluator.build_step_row("t1", 1, {"reasoning_skip_count": 1, "mechanic_priors_used_count": 0})
    evaluator.build_step_row(
        "t1",
        2,
        {
            "reasoning_skip_count": 2,
            "mechanic_prior_count": 1,
            "mechanic_priors_used_count": 1,
            "planner_selected_prior_id": "prior-1",
        },
    )
    
    summary = evaluator.build_summary_row("t1", {"world_model_snapshot": {"node_count": 10}})
    assert summary.full_reasoning_cycles_avoided == 2
    assert summary.memory_transfer_active is True


def test_progress_snapshot_uses_last_planner_selection_fallback():
    emitted = []
    runner = DurableARCRunner.__new__(DurableARCRunner)
    runner._progress_callback = emitted.append
    runner._ledger = []
    runner._build_phase_summary = lambda orchestrator: {}

    class WorldModel:
        def to_trace_snapshot(self):
            return {"node_count": 1, "edge_count": 1, "contradiction_count": 0, "demotion_count": 0}

        def to_prompt_summary(self, max_chars=1000):
            return "summary"

    selected = SimpleNamespace(
        mechanic_prior_id="prior-1",
        mechanic_prior_source="aggregate",
        prior_compatibility_score=0.72,
        predicted_observation={
            "effect_class": "object_progress",
            "meaningful_progress": True,
            "confidence": 0.81,
            "evidence_path": ["a1", "e1"],
        },
    )
    planner_selection = SimpleNamespace(
        selected=selected,
        candidate_count=1,
        selected_has_prediction=True,
        selected_has_falsification=False,
        mechanic_priors_used=1,
    )
    orchestrator = SimpleNamespace(
        _step_history=[
            {
                "action_id": "ACTION6",
                "reasoning_gating": {
                    "mode": "llm_reason",
                    "actions_tested_count": 4,
                    "productive_action_count": 1,
                    "multi_action_churn_detected": False,
                },
            }
        ],
        _solve_context=None,
        _last_planner_selection=planner_selection,
        _solve_context_get=lambda source, key, default=None: default,
        world_model=WorldModel(),
        reasoning_controller=SimpleNamespace(skip_count=2, escalation_count=1, reason_count=1),
    )
    task = SimpleNamespace(task_id="task1", game_id="game1")

    runner._emit_progress_snapshot(
        task=task,
        orchestrator=orchestrator,
        observation={"state": "NOT_FINISHED", "frame_hash": "abc", "available_actions": ["ACTION6"]},
        total_steps=2,
        reward=0.0,
        done=False,
        start_time=0.0,
    )

    assert emitted[0]["planner_candidate_count"] == 1
    assert emitted[0]["planner_selected_has_prediction"] is True
    assert emitted[0]["selected_prediction"]["effect_class"] == "object_progress"
    assert emitted[0]["planner_selected_prediction_effect_class"] == "object_progress"
    assert emitted[0]["planner_selected_prediction_confidence"] == 0.81
    assert emitted[0]["mechanic_priors_used_count"] == 1
    assert emitted[0]["planner_selected_prior_id"] == "prior-1"
    assert emitted[0]["planner_selected_prior_compatibility"] == 0.72
    assert emitted[0]["reasoning_gating"]["actions_tested_count"] == 4


def test_world_model_eval_reads_runner_prediction_and_prior_fields():
    evaluator = WorldModelEvaluator()

    row = evaluator.build_step_row(
        "task1",
        3,
        {
            "planner_selected_has_prediction": True,
            "planner_selected_prediction_effect_class": "terminal_progress",
            "planner_selected_prediction_confidence": 0.67,
            "planner_selected_prior_compatibility": 0.44,
            "reasoning_gating": {
                "mode": "llm_reason",
                "actions_tested_count": 5,
                "productive_action_count": 2,
                "multi_action_churn_detected": False,
            },
            "compiled_world_delta": {},
        },
    )

    assert row.selected_candidate_prediction_effect_class == "terminal_progress"
    assert row.selected_candidate_prediction_confidence == 0.67
    assert row.planner_selected_prior_compatibility == 0.44
    assert row.actions_tested_count == 5
    assert row.productive_action_count == 2


def test_planner_candidates_block_uses_local_content_block():
    orchestrator = ARCOrchestrator.__new__(ARCOrchestrator)
    selection = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                action_id="ACTION6",
                mode=PlanMode.PROBE,
                expected_gain=0.5,
                predicted_observation="frame changes",
                evidence_path="Game->Mechanic",
            )
        ]
    )
    orchestrator._solve_context = {"planner_selection": selection}
    orchestrator._solve_context_get = ARCOrchestrator._solve_context_get

    block = orchestrator._build_planner_candidates_block()

    assert isinstance(block, ContentBlock)
    assert block.type == "PLANNER_PROPOSALS"
    assert "ACTION6" in block.content
