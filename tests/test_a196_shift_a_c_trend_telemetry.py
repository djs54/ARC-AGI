"""Tests for A196: Shift-A/Shift-C compliance trend telemetry."""

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
    WorkflowDecision,
    WorkflowState,
)


class TestGraphQueryPortCapabilityMissingCounter:
    """Tests 1-2: capability_missing counter behavior in _call_tool."""

    def test_capability_missing_counter_sync_path(self):
        """Test 1a: Counter increments on sync exception path."""
        # Create a mock brain client that raises AttributeError (missing tool)
        class MockBrainClient:
            pass

        port = ArcGraphQueryPort(
            brain_client=MockBrainClient(),
            task_id="test",
            session_id="session1",
            strict=False,
        )

        assert port._capability_missing_count == 0

        # Call a tool that will fail due to missing method
        result = port._call_tool("fetch_goal_evidence", {"task_id": "test"})

        assert result.get("status") == "capability_missing"
        assert port._capability_missing_count == 1

    def test_capability_missing_counter_not_incremented_in_strict_mode(self):
        """Test 2: Counter does not increment when strict=True (raises instead)."""
        class MockBrainClient:
            pass

        port = ArcGraphQueryPort(
            brain_client=MockBrainClient(),
            task_id="test",
            session_id="session1",
            strict=True,
        )

        assert port._capability_missing_count == 0

        # In strict mode, should raise instead of returning capability_missing
        with pytest.raises(RuntimeError, match="required ARC tool missing"):
            port._call_tool("fetch_goal_evidence", {"task_id": "test"})

        # Counter should not have incremented
        assert port._capability_missing_count == 0

    def test_pop_capability_missing_count_returns_and_resets(self):
        """Test 1b: pop_capability_missing_count() returns accumulated count and resets."""
        class MockBrainClient:
            pass

        port = ArcGraphQueryPort(
            brain_client=MockBrainClient(),
            task_id="test",
            session_id="session1",
            strict=False,
        )

        # Trigger two capability_missing errors
        port._call_tool("fetch_goal_evidence", {"task_id": "test"})
        port._call_tool("fetch_action_evidence", {"task_id": "test", "action_id": "ACTION1"})

        # First pop should return 2
        count = port.pop_capability_missing_count()
        assert count == 2

        # Second immediate pop should return 0 (reset)
        count2 = port.pop_capability_missing_count()
        assert count2 == 0


class TestStepSnapshotNewFields:
    """Tests 3-6: _step_snapshot includes new fields."""

    def test_llm_escalated_plan_true(self):
        """Test 3a: llm_escalated_plan=True when execution.candidate.metadata has llm_guidance: True."""
        telemetry = ArcV2Telemetry(
            task_id="test_task",
            game_id="test_game",
            append_snapshot=None,
        )

        candidate = PlanCandidate(
            action_id="ACTION1",
            metadata={"llm_guidance": True},
        )
        execution = ExecutionResult(
            action_id="ACTION1",
            candidate=candidate,
            observation={},
        )
        telemetry._latest_execution = execution

        state = WorkflowState()
        snapshot = telemetry._step_snapshot((state,))

        assert "llm_escalated_plan" in snapshot
        assert snapshot["llm_escalated_plan"] is True

    def test_llm_escalated_plan_false(self):
        """Test 3b: llm_escalated_plan=False when llm_guidance absent or False."""
        telemetry = ArcV2Telemetry(
            task_id="test_task",
            game_id="test_game",
            append_snapshot=None,
        )

        candidate = PlanCandidate(
            action_id="ACTION1",
            metadata={},
        )
        execution = ExecutionResult(
            action_id="ACTION1",
            candidate=candidate,
            observation={},
        )
        telemetry._latest_execution = execution

        state = WorkflowState()
        snapshot = telemetry._step_snapshot((state,))

        assert "llm_escalated_plan" in snapshot
        assert snapshot["llm_escalated_plan"] is False

    def test_graph_grounded_with_evidence(self):
        """Test 4a: graph_grounded=True when graph_evidence is non-empty."""
        telemetry = ArcV2Telemetry(
            task_id="test_task",
            game_id="test_game",
            append_snapshot=None,
        )

        candidate = PlanCandidate(
            action_id="ACTION1",
            metadata={"graph_evidence": [{"goal_id": "goal1", "confidence": 0.8}]},
        )
        execution = ExecutionResult(
            action_id="ACTION1",
            candidate=candidate,
            observation={},
        )
        telemetry._latest_execution = execution

        state = WorkflowState()
        snapshot = telemetry._step_snapshot((state,))

        assert "graph_grounded" in snapshot
        assert snapshot["graph_grounded"] is True

    def test_graph_grounded_with_entity_neighborhood(self):
        """Test 4b: graph_grounded=True when entity_neighborhood_grounded is present and truthy."""
        telemetry = ArcV2Telemetry(
            task_id="test_task",
            game_id="test_game",
            append_snapshot=None,
        )

        candidate = PlanCandidate(
            action_id="ACTION1",
            metadata={"entity_neighborhood_grounded": True},
        )
        execution = ExecutionResult(
            action_id="ACTION1",
            candidate=candidate,
            observation={},
        )
        telemetry._latest_execution = execution

        state = WorkflowState()
        snapshot = telemetry._step_snapshot((state,))

        assert "graph_grounded" in snapshot
        assert snapshot["graph_grounded"] is True

    def test_graph_grounded_false(self):
        """Test 4c: graph_grounded=False when both signals absent or empty."""
        telemetry = ArcV2Telemetry(
            task_id="test_task",
            game_id="test_game",
            append_snapshot=None,
        )

        candidate = PlanCandidate(
            action_id="ACTION1",
            metadata={"graph_evidence": []},
        )
        execution = ExecutionResult(
            action_id="ACTION1",
            candidate=candidate,
            observation={},
        )
        telemetry._latest_execution = execution

        state = WorkflowState()
        snapshot = telemetry._step_snapshot((state,))

        assert "graph_grounded" in snapshot
        assert snapshot["graph_grounded"] is False

    def test_graph_grounded_false_for_purely_negative_evidence(self):
        """Regression test (2026-08-25, live-smoke-discovered): the real
        production shape of PlanCandidate.metadata["graph_evidence"]
        (plan_generator.py::_build_candidates, via
        graph_port.fetch_per_action_evidence) is a dict like
        {"confidence": 0.0, "contradictions": 64, "supports": 0} for an
        action the graph has confirmed does NOT work -- non-empty, but the
        opposite of "grounded." The pre-fix bool(graph_evidence) check
        counted this as graph_grounded=True (confirmed live: a 60-step
        smoke run where every single candidate had 0 confidence / dozens of
        contradictions / 0 supports still reported 100% graph_grounded_
        decision_rate), which made the KPI meaningless for judging whether
        the graph is actually steering decisions well. Purely negative
        evidence must report graph_grounded=False."""
        telemetry = ArcV2Telemetry(task_id="test_task", game_id="test_game", append_snapshot=None)
        candidate = PlanCandidate(
            action_id="ACTION6",
            metadata={
                "graph_evidence": {
                    "attempts": 66,
                    "confidence": 0.0,
                    "contradictions": 64,
                    "supports": 0,
                    "raw": {"falsified_count": 64, "confidence": 0.0},
                },
            },
        )
        execution = ExecutionResult(action_id="ACTION6", candidate=candidate, observation={})
        telemetry._latest_execution = execution

        snapshot = telemetry._step_snapshot((WorkflowState(),))

        assert snapshot["graph_grounded"] is False

    def test_graph_grounded_true_for_real_positive_confidence(self):
        """Companion to the regression test above: the same real dict shape
        with genuine positive confidence must still report grounded=True --
        the fix distinguishes positive from negative, it doesn't just flip
        the sign."""
        telemetry = ArcV2Telemetry(task_id="test_task", game_id="test_game", append_snapshot=None)
        candidate = PlanCandidate(
            action_id="ACTION6",
            metadata={
                "graph_evidence": {"attempts": 5, "confidence": 0.6, "contradictions": 1, "supports": 4},
            },
        )
        execution = ExecutionResult(action_id="ACTION6", candidate=candidate, observation={})
        telemetry._latest_execution = execution

        snapshot = telemetry._step_snapshot((WorkflowState(),))

        assert snapshot["graph_grounded"] is True

    def test_graph_grounded_true_when_supports_outweigh_contradictions_despite_zero_confidence(self):
        """Edge case: confidence can be 0.0 while supports still outweigh
        contradictions (e.g. a freshly-transferred rule with supports=2,
        contradictions=0, and confidence not yet computed) -- grounded on
        the supports>contradictions signal alone, not confidence alone."""
        telemetry = ArcV2Telemetry(task_id="test_task", game_id="test_game", append_snapshot=None)
        candidate = PlanCandidate(
            action_id="ACTION6",
            metadata={
                "graph_evidence": {"attempts": 2, "confidence": 0.0, "contradictions": 0, "supports": 2},
            },
        )
        execution = ExecutionResult(action_id="ACTION6", candidate=candidate, observation={})
        telemetry._latest_execution = execution

        snapshot = telemetry._step_snapshot((WorkflowState(),))

        assert snapshot["graph_grounded"] is True

    def test_exhaustion_source_present(self):
        """Test 5a: exhaustion_source reflects evaluation.metadata.get('exhaustion_source') when present."""
        telemetry = ArcV2Telemetry(
            task_id="test_task",
            game_id="test_game",
            append_snapshot=None,
        )

        evaluation = EvaluationResult(
            decision=WorkflowDecision.CONTINUE,
            meaningful_progress=False,
            metadata={"exhaustion_source": "graph_confirmed_no_untested"},
        )
        telemetry._latest_evaluation = evaluation

        state = WorkflowState()
        snapshot = telemetry._step_snapshot((state,))

        assert "exhaustion_source" in snapshot
        assert snapshot["exhaustion_source"] == "graph_confirmed_no_untested"

    def test_exhaustion_source_absent(self):
        """Test 5b: exhaustion_source is None when absent."""
        telemetry = ArcV2Telemetry(
            task_id="test_task",
            game_id="test_game",
            append_snapshot=None,
        )

        evaluation = EvaluationResult(
            decision=WorkflowDecision.CONTINUE,
            meaningful_progress=False,
            metadata={},
        )
        telemetry._latest_evaluation = evaluation

        state = WorkflowState()
        snapshot = telemetry._step_snapshot((state,))

        assert "exhaustion_source" in snapshot
        assert snapshot["exhaustion_source"] is None

    def test_capability_missing_count_with_port(self):
        """Test 6a: capability_missing_count from pop_capability_missing_count() when port present."""
        class MockBrainClient:
            pass

        port = ArcGraphQueryPort(
            brain_client=MockBrainClient(),
            task_id="test",
            session_id="session1",
            strict=False,
        )

        # Trigger a capability_missing error
        port._call_tool("fetch_goal_evidence", {"task_id": "test"})

        telemetry = ArcV2Telemetry(
            task_id="test_task",
            game_id="test_game",
            append_snapshot=None,
        )
        telemetry._graph_query_port = port

        state = WorkflowState()
        snapshot = telemetry._step_snapshot((state,))

        assert "capability_missing_count" in snapshot
        assert snapshot["capability_missing_count"] == 1

        # Verify that calling again returns 0 (counter was reset)
        snapshot2 = telemetry._step_snapshot((state,))
        assert snapshot2["capability_missing_count"] == 0

    def test_capability_missing_count_without_port(self):
        """Test 6b: capability_missing_count defaults to 0 when port absent."""
        telemetry = ArcV2Telemetry(
            task_id="test_task",
            game_id="test_game",
            append_snapshot=None,
        )
        telemetry._graph_query_port = None

        state = WorkflowState()
        snapshot = telemetry._step_snapshot((state,))

        assert "capability_missing_count" in snapshot
        assert snapshot["capability_missing_count"] == 0

    def test_capability_missing_count_with_broken_method(self):
        """Test 6c: capability_missing_count is 0 when method is missing or raises."""
        class BrokenPort:
            # No pop_capability_missing_count method
            pass

        telemetry = ArcV2Telemetry(
            task_id="test_task",
            game_id="test_game",
            append_snapshot=None,
        )
        telemetry._graph_query_port = BrokenPort()

        state = WorkflowState()
        snapshot = telemetry._step_snapshot((state,))

        assert "capability_missing_count" in snapshot
        assert snapshot["capability_missing_count"] == 0


class TestGraphComplianceReportScript:
    """Tests 7-8: scripts/graph_compliance_report.py behavior."""

    def test_report_function_with_fixture_steps(self):
        """Test 7: report() produces correct rates for all metrics."""
        from scripts.graph_compliance_report import report

        # Construct fixture step list covering all metrics
        steps = [
            {
                "snapshot_type": "step",
                "step": 1,
                "reasoning_escalation_count": 1,
                "llm_escalated_plan": False,
                "graph_grounded": True,
                "capability_missing_count": 0,
                "compliance_violation_count": 0,
            },
            {
                "snapshot_type": "step",
                "step": 2,
                "reasoning_escalation_count": 0,
                "llm_escalated_plan": True,
                "graph_grounded": True,
                "capability_missing_count": 1,
                "compliance_violation_count": 0,
                "exhaustion_source": None,
            },
            {
                "snapshot_type": "step",
                "step": 3,
                "reasoning_escalation_count": 0,
                "llm_escalated_plan": True,
                "graph_grounded": False,
                "capability_missing_count": 0,
                "compliance_violation_count": 1,
                "exhaustion_source": "graph_confirmed_no_untested",
            },
            {
                "snapshot_type": "step",
                "step": 4,
                "reasoning_escalation_count": 0,
                "llm_escalated_plan": False,
                "graph_grounded": False,
                "capability_missing_count": 0,
                "compliance_violation_count": 0,
                "exhaustion_source": "threshold_only",
            },
        ]

        result = report(steps)

        # Verify all metrics are present
        assert "total_steps" in result
        assert result["total_steps"] == 4

        assert "llm_escalation_rate_goal_per_100" in result
        assert result["llm_escalation_rate_goal_per_100"] == 25.0  # 1 out of 4

        assert "llm_escalation_rate_plan_per_100" in result
        assert result["llm_escalation_rate_plan_per_100"] == 50.0  # 2 out of 4

        assert "graph_grounded_decision_rate" in result
        assert result["graph_grounded_decision_rate"] == 50.0  # 2 out of 4

        assert "capability_missing_total" in result
        assert result["capability_missing_total"] == 1

        assert "compliance_violation_total" in result
        assert result["compliance_violation_total"] == 1

        assert "exhaustion_source_breakdown" in result
        assert result["exhaustion_source_breakdown"] == {
            "graph_confirmed_no_untested": 1,
            "threshold_only": 1,
        }

    def test_report_empty_steps_no_division_by_zero(self):
        """Test 8: report() on empty step list returns safe structure without dividing by zero."""
        from scripts.graph_compliance_report import report

        result = report([])

        assert result == {"total_steps": 0}
        # Should not crash, just return minimal safe structure

    def test_script_loads_json_array_format(self):
        """Integration test: script correctly loads JSON array format (not JSON-lines)."""
        from scripts.graph_compliance_report import load_steps

        # Create a temporary JSON array trace file
        trace_data = [
            {
                "snapshot_type": "phase_transition",
                "step": 0,
                "phase": "setup",
            },
            {
                "snapshot_type": "step",
                "step": 1,
                "reasoning_escalation_count": 0,
                "llm_escalated_plan": False,
                "graph_grounded": True,
                "capability_missing_count": 0,
                "compliance_violation_count": 0,
            },
            {
                "snapshot_type": "step",
                "step": 2,
                "reasoning_escalation_count": 0,
                "llm_escalated_plan": True,
                "graph_grounded": False,
                "capability_missing_count": 0,
                "compliance_violation_count": 0,
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            trace_path = Path(tmpdir) / "agent_execution_trace.json"
            trace_path.write_text(json.dumps(trace_data))

            steps = load_steps(trace_path)

            # Should only return step snapshots, not phase_transition
            assert len(steps) == 2
            assert all(s.get("snapshot_type") == "step" for s in steps)
