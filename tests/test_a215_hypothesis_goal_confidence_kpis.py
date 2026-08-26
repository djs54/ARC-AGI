"""Tests for A215 Track C: KPI instrumentation for hypothesis confirmation/contradiction and goal confidence updates."""

import json
import tempfile
from pathlib import Path
from typing import Mapping

import pytest

from agents.arc4.graph_queries import ArcGraphQueryPort
from agents.arc4.telemetry import ArcV2Telemetry
from agents.arc4.types import (
    EvaluationResult,
    ExecutionResult,
    PlanCandidate,
    ResolvedGoal,
    GoalHypothesis,
    VetDecision,
    WorkflowDecision,
    WorkflowState,
)


class TestHypothesisConfirmContradictCounter:
    """Tests for _hypothesis_confirm_contradict_count behavior in _call_tool."""

    def test_hypothesis_confirm_contradict_counter_increments_on_confirm(self):
        """Test: Counter increments when record_vet is called with approved=True."""
        class MockBrainClient:
            def arc_confirm_hypothesis(self, **kwargs):
                return {"status": "ok"}

        port = ArcGraphQueryPort(
            brain_client=MockBrainClient(),
            task_id="test",
            session_id="session1",
            strict=False,
        )

        assert port._hypothesis_confirm_contradict_count == 0

        # Create a vet decision with approved=True (needs candidate with action_id)
        candidate = PlanCandidate(action_id="ACTION1", metadata={})
        vet = VetDecision(
            approved=True,
            reason="test reason",
            candidate=candidate,
            metadata={},
        )
        port.record_vet(vet)

        assert port._hypothesis_confirm_contradict_count == 1

    def test_hypothesis_confirm_contradict_counter_increments_on_contradict(self):
        """Test: Counter increments when record_vet is called with approved=False."""
        class MockBrainClient:
            def arc_contradict_hypothesis(self, **kwargs):
                return {"status": "ok"}

        port = ArcGraphQueryPort(
            brain_client=MockBrainClient(),
            task_id="test",
            session_id="session1",
            strict=False,
        )

        assert port._hypothesis_confirm_contradict_count == 0

        # Create a vet decision with approved=False (needs candidate with action_id)
        candidate = PlanCandidate(action_id="ACTION1", metadata={})
        vet = VetDecision(
            approved=False,
            reason="test reason",
            candidate=candidate,
            metadata={},
        )
        port.record_vet(vet)

        assert port._hypothesis_confirm_contradict_count == 1

    def test_pop_hypothesis_confirm_contradict_count_returns_and_resets(self):
        """Test: pop_hypothesis_confirm_contradict_count() returns accumulated count and resets."""
        class MockBrainClient:
            def arc_confirm_hypothesis(self, **kwargs):
                return {"status": "ok"}
            def arc_contradict_hypothesis(self, **kwargs):
                return {"status": "ok"}

        port = ArcGraphQueryPort(
            brain_client=MockBrainClient(),
            task_id="test",
            session_id="session1",
            strict=False,
        )

        # Trigger one confirm and one contradict
        candidate1 = PlanCandidate(action_id="ACTION1", metadata={})
        candidate2 = PlanCandidate(action_id="ACTION2", metadata={})
        vet1 = VetDecision(approved=True, reason="test", candidate=candidate1, metadata={})
        vet2 = VetDecision(approved=False, reason="test", candidate=candidate2, metadata={})
        port.record_vet(vet1)
        port.record_vet(vet2)

        # First pop should return 2
        count = port.pop_hypothesis_confirm_contradict_count()
        assert count == 2

        # Second immediate pop should return 0 (reset)
        count2 = port.pop_hypothesis_confirm_contradict_count()
        assert count2 == 0


class TestGoalConfidenceWriteCounter:
    """Tests for _goal_confidence_write_count behavior in _call_tool."""

    def test_goal_confidence_write_counter_increments_with_goal_id(self):
        """Test: Counter increments when record_evaluation is called with goal_id."""
        class MockBrainClient:
            def arc_update_goal_confidence(self, **kwargs):
                return {"status": "ok"}

        port = ArcGraphQueryPort(
            brain_client=MockBrainClient(),
            task_id="test",
            session_id="session1",
            strict=False,
        )

        assert port._goal_confidence_write_count == 0

        # Create an evaluation with goal_id in metadata
        evaluation = EvaluationResult(
            decision=WorkflowDecision.CONTINUE,
            meaningful_progress=True,
            reason="test",
            metadata={"goal_id": "GOAL1"},
        )
        port.record_evaluation(evaluation)

        assert port._goal_confidence_write_count == 1

    def test_goal_confidence_write_counter_does_not_increment_without_goal_id(self):
        """Test: Counter does not increment when record_evaluation is called without goal_id."""
        class MockBrainClient:
            pass

        port = ArcGraphQueryPort(
            brain_client=MockBrainClient(),
            task_id="test",
            session_id="session1",
            strict=False,
        )

        assert port._goal_confidence_write_count == 0

        # Create an evaluation without goal_id
        evaluation = EvaluationResult(
            decision=WorkflowDecision.CONTINUE,
            meaningful_progress=True,
            reason="test",
            metadata={},
        )
        port.record_evaluation(evaluation)

        # Counter should still be 0 (no goal_id means no update_goal_confidence call)
        assert port._goal_confidence_write_count == 0

    def test_pop_goal_confidence_write_count_returns_and_resets(self):
        """Test: pop_goal_confidence_write_count() returns accumulated count and resets."""
        class MockBrainClient:
            def arc_update_goal_confidence(self, **kwargs):
                return {"status": "ok"}

        port = ArcGraphQueryPort(
            brain_client=MockBrainClient(),
            task_id="test",
            session_id="session1",
            strict=False,
        )

        # Trigger two goal confidence writes
        eval1 = EvaluationResult(
            decision=WorkflowDecision.CONTINUE,
            meaningful_progress=True,
            reason="test",
            metadata={"goal_id": "GOAL1"},
        )
        eval2 = EvaluationResult(
            decision=WorkflowDecision.CONTINUE,
            meaningful_progress=False,
            reason="test",
            metadata={"goal_id": "GOAL2"},
        )
        port.record_evaluation(eval1)
        port.record_evaluation(eval2)

        # First pop should return 2
        count = port.pop_goal_confidence_write_count()
        assert count == 2

        # Second immediate pop should return 0 (reset)
        count2 = port.pop_goal_confidence_write_count()
        assert count2 == 0


class TestStepSnapshotNewFields:
    """Tests for new fields in _step_snapshot."""

    def test_hypothesis_confirm_contradict_attempted_count_in_snapshot(self):
        """Test: hypothesis_confirm_contradict_attempted_count appears in snapshot."""
        class MockBrainClient:
            def arc_confirm_hypothesis(self, **kwargs):
                return {"status": "ok"}

        port = ArcGraphQueryPort(
            brain_client=MockBrainClient(),
            task_id="test",
            session_id="session1",
            strict=False,
        )

        telemetry = ArcV2Telemetry(
            task_id="test_task",
            game_id="test_game",
            append_snapshot=None,
        )
        telemetry._graph_query_port = port

        # Trigger a vet call with candidate
        candidate = PlanCandidate(action_id="ACTION1", metadata={})
        vet = VetDecision(approved=True, reason="test", candidate=candidate, metadata={})
        port.record_vet(vet)

        state = WorkflowState()
        snapshot = telemetry._step_snapshot((state,))

        assert "hypothesis_confirm_contradict_attempted_count" in snapshot
        assert snapshot["hypothesis_confirm_contradict_attempted_count"] == 1

    def test_goal_confidence_write_attempted_count_in_snapshot(self):
        """Test: goal_confidence_write_attempted_count appears in snapshot."""
        class MockBrainClient:
            def arc_update_goal_confidence(self, **kwargs):
                return {"status": "ok"}

        port = ArcGraphQueryPort(
            brain_client=MockBrainClient(),
            task_id="test",
            session_id="session1",
            strict=False,
        )

        telemetry = ArcV2Telemetry(
            task_id="test_task",
            game_id="test_game",
            append_snapshot=None,
        )
        telemetry._graph_query_port = port

        # Trigger a goal confidence write
        evaluation = EvaluationResult(
            decision=WorkflowDecision.CONTINUE,
            meaningful_progress=True,
            reason="test",
            metadata={"goal_id": "GOAL1"},
        )
        port.record_evaluation(evaluation)

        state = WorkflowState()
        snapshot = telemetry._step_snapshot((state,))

        assert "goal_confidence_write_attempted_count" in snapshot
        assert snapshot["goal_confidence_write_attempted_count"] == 1

    def test_new_fields_reset_to_zero_on_second_snapshot(self):
        """Test: Counters reset to 0 after popping in snapshot."""
        class MockBrainClient:
            def arc_confirm_hypothesis(self, **kwargs):
                return {"status": "ok"}
            def arc_update_goal_confidence(self, **kwargs):
                return {"status": "ok"}

        port = ArcGraphQueryPort(
            brain_client=MockBrainClient(),
            task_id="test",
            session_id="session1",
            strict=False,
        )

        telemetry = ArcV2Telemetry(
            task_id="test_task",
            game_id="test_game",
            append_snapshot=None,
        )
        telemetry._graph_query_port = port

        # First snapshot with data
        candidate = PlanCandidate(action_id="ACTION1", metadata={})
        vet = VetDecision(approved=True, reason="test", candidate=candidate, metadata={})
        eval_res = EvaluationResult(
            decision=WorkflowDecision.CONTINUE,
            meaningful_progress=True,
            reason="test",
            metadata={"goal_id": "GOAL1"},
        )
        port.record_vet(vet)
        port.record_evaluation(eval_res)

        state = WorkflowState()
        snapshot1 = telemetry._step_snapshot((state,))
        assert snapshot1["hypothesis_confirm_contradict_attempted_count"] == 1
        assert snapshot1["goal_confidence_write_attempted_count"] == 1

        # Second snapshot should have 0s (counters reset after popping)
        snapshot2 = telemetry._step_snapshot((state,))
        assert snapshot2["hypothesis_confirm_contradict_attempted_count"] == 0
        assert snapshot2["goal_confidence_write_attempted_count"] == 0


class TestReportNewRates:
    """Tests for new rate calculations in report()."""

    def test_report_with_hypothesis_confirm_contradict_steps(self):
        """Test: hypothesis_confirm_contradict_rate_per_100 computes correctly."""
        from scripts.graph_compliance_report import report

        steps = [
            {"hypothesis_confirm_contradict_attempted_count": 1},
            {"hypothesis_confirm_contradict_attempted_count": 0},
            {"hypothesis_confirm_contradict_attempted_count": 1},
        ]

        result = report(steps)
        # 2 out of 3 steps have the count > 0 → 66.67%
        assert result["hypothesis_confirm_contradict_rate_per_100"] == round(100 * 2 / 3, 2)

    def test_report_with_goal_confidence_write_steps(self):
        """Test: goal_confidence_write_rate_per_100 computes correctly."""
        from scripts.graph_compliance_report import report

        steps = [
            {"goal_confidence_write_attempted_count": 1},
            {"goal_confidence_write_attempted_count": 1},
            {"goal_confidence_write_attempted_count": 0},
        ]

        result = report(steps)
        # 2 out of 3 steps have the count > 0 → 66.67%
        assert result["goal_confidence_write_rate_per_100"] == round(100 * 2 / 3, 2)

    def test_report_missing_new_keys_defaults_to_zero(self):
        """Test: Missing new keys default to 0.0 rates (backward compatibility)."""
        from scripts.graph_compliance_report import report

        # Simulate older trace snapshots without the new fields
        steps = [
            {"step": 1},
            {"step": 2},
            {"step": 3},
        ]

        result = report(steps)
        # No steps have these fields → 0.0%
        assert result["hypothesis_confirm_contradict_rate_per_100"] == 0.0
        assert result["goal_confidence_write_rate_per_100"] == 0.0

    def test_report_mixed_old_and_new_steps(self):
        """Test: Mixed old (no field) and new (with field) steps compute correctly."""
        from scripts.graph_compliance_report import report

        steps = [
            {},  # Old step, no field
            {"hypothesis_confirm_contradict_attempted_count": 1},  # New step with data
            {"goal_confidence_write_attempted_count": 1},  # New step with data
            {},  # Old step
        ]

        result = report(steps)
        # 1 out of 4 steps have hypothesis count > 0 → 25.0%
        assert result["hypothesis_confirm_contradict_rate_per_100"] == 25.0
        # 1 out of 4 steps have goal write count > 0 → 25.0%
        assert result["goal_confidence_write_rate_per_100"] == 25.0

    def test_existing_rates_unaffected_by_new_fields(self):
        """Test: Existing metrics are unaffected by new fields (regression test)."""
        from scripts.graph_compliance_report import report

        steps = [
            {
                "reasoning_escalation_count": 0,
                "llm_escalated_plan": False,
                "graph_grounded": True,
                "graph_informed": True,
                "hypothesis_confirm_contradict_attempted_count": 1,
                "goal_confidence_write_attempted_count": 1,
            },
            {
                "reasoning_escalation_count": 1,
                "llm_escalated_plan": True,
                "graph_grounded": False,
                "graph_informed": False,
                "hypothesis_confirm_contradict_attempted_count": 0,
                "goal_confidence_write_attempted_count": 0,
            },
        ]

        result = report(steps)
        # Verify existing rates are unaffected
        assert result["llm_escalation_rate_goal_per_100"] == 50.0
        assert result["llm_escalation_rate_plan_per_100"] == 50.0
        assert result["graph_grounded_decision_rate"] == 50.0
        assert result["graph_informed_decision_rate"] == 50.0
        # And new rates are correct
        assert result["hypothesis_confirm_contradict_rate_per_100"] == 50.0
        assert result["goal_confidence_write_rate_per_100"] == 50.0
