"""Tests for A129 Temporal workflow and activities."""

import pytest

from agents.arc4.types import (
    WorkflowState,
    PerceptionSnapshot,
    ResolvedGoal,
    GoalHypothesis,
    PlanningResult,
    PlanCandidate,
    VetDecision,
    ExecutionResult,
    EvaluationResult,
    WorkflowDecision,
    PhaseResult,
    WorkflowPhase,
    PhaseStatus,
    WorkflowRunResult,
    WorkflowStatus,
)


class TestSerialization:
    """Test serialization round-trip for all types."""

    def test_perceived_entity_serialization(self):
        """Verify PerceivedEntity to_dict/from_dict round-trip."""
        from agents.arc4.types import PerceivedEntity

        entity = PerceivedEntity(kind="object", value="red_block", attributes={"size": 3})
        d = entity.to_dict()
        entity2 = PerceivedEntity.from_dict(d)
        assert entity == entity2

    def test_perception_snapshot_serialization(self):
        """Verify PerceptionSnapshot to_dict/from_dict round-trip."""
        perception = PerceptionSnapshot(
            observation={"grid": [[0, 1], [2, 3]]},
            grid_hash="abc123",
            grid_shape=(2, 2),
            loop_signal=True,
            repeated_grid_count=1,
        )
        d = perception.to_dict()
        perception2 = PerceptionSnapshot.from_dict(d)
        assert perception == perception2

    def test_goal_hypothesis_serialization(self):
        """Verify GoalHypothesis to_dict/from_dict round-trip."""
        hyp = GoalHypothesis(
            goal_id="goal_1",
            description="Find red block",
            confidence=0.85,
            evidence=("observation_1", "observation_2"),
        )
        d = hyp.to_dict()
        hyp2 = GoalHypothesis.from_dict(d)
        assert hyp == hyp2

    def test_resolved_goal_serialization(self):
        """Verify ResolvedGoal to_dict/from_dict round-trip."""
        goal = ResolvedGoal(
            selected=GoalHypothesis(goal_id="goal_1", description="Find red", confidence=0.9),
            alternatives=(GoalHypothesis(goal_id="goal_2", description="Find blue", confidence=0.5),),
            grounding_gate_passed=True,
        )
        d = goal.to_dict()
        goal2 = ResolvedGoal.from_dict(d)
        assert goal == goal2

    def test_plan_candidate_serialization(self):
        """Verify PlanCandidate to_dict/from_dict round-trip."""
        candidate = PlanCandidate(
            action_id="action_1",
            goal_id="goal_1",
            score=0.95,
            rationale="Move left",
            expected_effect="Red block moves left",
        )
        d = candidate.to_dict()
        candidate2 = PlanCandidate.from_dict(d)
        assert candidate == candidate2

    def test_planning_result_serialization(self):
        """Verify PlanningResult to_dict/from_dict round-trip."""
        candidate = PlanCandidate(action_id="action_1", score=0.9)
        result = PlanningResult(
            candidate=candidate,
            alternatives=(PlanCandidate(action_id="action_2", score=0.7),),
            needs_vet=True,
        )
        d = result.to_dict()
        result2 = PlanningResult.from_dict(d)
        assert result == result2

    def test_vet_decision_serialization(self):
        """Verify VetDecision to_dict/from_dict round-trip."""
        decision = VetDecision(
            approved=True,
            candidate=PlanCandidate(action_id="action_1", score=0.9),
            reason="Confidence high enough",
        )
        d = decision.to_dict()
        decision2 = VetDecision.from_dict(d)
        assert decision == decision2

    def test_execution_result_serialization(self):
        """Verify ExecutionResult to_dict/from_dict round-trip."""
        result = ExecutionResult(
            action_id="action_1",
            candidate=PlanCandidate(action_id="action_1", score=0.9),
            observation={"grid": [[0, 1]]},
            did_progress=True,
            actual_effect="Red block moved left",
        )
        d = result.to_dict()
        result2 = ExecutionResult.from_dict(d)
        assert result == result2

    def test_evaluation_result_serialization(self):
        """Verify EvaluationResult to_dict/from_dict round-trip."""
        result = EvaluationResult(
            decision=WorkflowDecision.CONTINUE,
            meaningful_progress=True,
            falsification_delta=0,
            reason="Progress detected",
        )
        d = result.to_dict()
        result2 = EvaluationResult.from_dict(d)
        assert result == result2

    def test_phase_result_serialization(self):
        """Verify PhaseResult to_dict/from_dict round-trip."""
        phase_result = PhaseResult(
            phase=WorkflowPhase.PERCEIVE,
            status=PhaseStatus.OK,
            payload=PerceptionSnapshot(observation={}, grid_hash="abc"),
            reason=None,
        )
        d = phase_result.to_dict()
        phase_result2 = PhaseResult.from_dict(d)
        assert phase_result.phase == phase_result2.phase
        assert phase_result.status == phase_result2.status

    def test_workflow_state_serialization(self):
        """Verify WorkflowState to_dict/from_dict round-trip."""
        state = WorkflowState(
            step_index=5,
            phase_runs=30,
            terminated=False,
            termination_state=WorkflowStatus.RUNNING,
            action_attempt_counts={"action_1": 3, "action_2": 1},
            consecutive_no_progress_count=2,
        )
        d = state.to_dict()
        state2 = WorkflowState.from_dict(d)
        assert state == state2

    def test_workflow_run_result_serialization(self):
        """Verify WorkflowRunResult to_dict/from_dict round-trip."""
        result = WorkflowRunResult(
            status=WorkflowStatus.TERMINATED,
            state=WorkflowState(step_index=10),
            reason="Goal reached",
            completed_cycles=10,
        )
        d = result.to_dict()
        result2 = WorkflowRunResult.from_dict(d)
        assert result == result2


class TestActivityConfiguration:
    """Test activity configuration and timeouts."""

    @pytest.mark.skipif(
        __import__("importlib.util").util.find_spec("temporalio") is None,
        reason="temporalio not installed",
    )
    def test_activity_imports(self):
        """Verify all activities are importable."""
        from agents.arc4.temporal_activities import (
            perceive_activity,
            resolve_activity,
            plan_activity,
            vet_activity,
            execute_activity,
            evaluate_activity,
        )
        assert perceive_activity is not None
        assert resolve_activity is not None
        assert plan_activity is not None
        assert vet_activity is not None
        assert execute_activity is not None
        assert evaluate_activity is not None

    @pytest.mark.skipif(
        __import__("importlib.util").util.find_spec("temporalio") is None,
        reason="temporalio not installed",
    )
    def test_workflow_imports(self):
        """Verify workflow is importable."""
        from agents.arc4.temporal_workflows import ArcPuzzleWorkflow
        assert ArcPuzzleWorkflow is not None


class TestPhaseRegistration:
    """Test phase registry system."""

    @pytest.mark.skipif(
        __import__("importlib.util").util.find_spec("temporalio") is None,
        reason="temporalio not installed",
    )
    def test_phase_registration(self):
        """Verify phases can be registered."""
        from agents.arc4.temporal_activities import register_phases, _get_phase

        phases = {
            "perceive": lambda state, obs: None,
            "resolve": lambda state, perc: None,
        }
        register_phases(phases)
        assert _get_phase("perceive") is not None
        assert _get_phase("resolve") is not None
