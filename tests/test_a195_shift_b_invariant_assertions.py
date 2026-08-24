"""Tests for A195: Shift-B invariant assertion on real run data."""

import json
import tempfile
from pathlib import Path
from typing import Mapping

import pytest

from agents.arc4.compliance_checks import check_shift_b_invariants
from agents.arc4.evaluator import Evaluator
from agents.arc4.telemetry import ArcV2Telemetry
from agents.arc4.types import (
    EvaluationResult,
    ExecutionResult,
    PlanCandidate,
    ResolvedGoal,
    GoalHypothesis,
    WorkflowDecision,
    WorkflowState,
    PerceptionSnapshot,
)


class TestCheckShiftBInvariants:
    """Pure check_shift_b_invariants function tests."""

    def test_clean_candidate_no_repeated_falsified_key(self):
        """Test 1: Returns [] for a candidate with repeated_falsified absent."""
        candidate = PlanCandidate(
            action_id="ACTION1",
            metadata={"other_key": "value"},
        )
        execution = ExecutionResult(
            action_id="ACTION1",
            candidate=candidate,
            observation={},
        )
        violations = check_shift_b_invariants(execution)
        assert violations == []

    def test_clean_candidate_repeated_falsified_false(self):
        """Test 1: Returns [] for a candidate with repeated_falsified=False."""
        candidate = PlanCandidate(
            action_id="ACTION1",
            metadata={"repeated_falsified": False},
        )
        execution = ExecutionResult(
            action_id="ACTION1",
            candidate=candidate,
            observation={},
        )
        violations = check_shift_b_invariants(execution)
        assert violations == []

    def test_violating_candidate_repeated_falsified_true(self):
        """Test 2: Returns violation string when repeated_falsified=True, contains action_id."""
        candidate = PlanCandidate(
            action_id="ACTION1",
            book_id="ACTION1@x,y",
            metadata={"repeated_falsified": True},
        )
        execution = ExecutionResult(
            action_id="ACTION1",
            candidate=candidate,
            observation={},
        )
        violations = check_shift_b_invariants(execution)
        assert len(violations) == 1
        assert "ACTION1" in violations[0]
        assert "repeated_falsified" in violations[0]

    def test_no_candidate(self):
        """Test 3: Returns [] defensively for execution with candidate=None."""
        execution = ExecutionResult(
            action_id="ACTION1",
            candidate=None,
            observation={},
        )
        violations = check_shift_b_invariants(execution)
        assert violations == []



class TestEvaluatorIntegration:
    """Test 4: Verify evaluator.py::evaluate() integration."""

    def test_evaluate_with_violating_candidate(self):
        """Evaluator attaches compliance_violations to metadata without changing decision/reason/falsification_delta."""
        evaluator = Evaluator()
        state = WorkflowState()

        # Create a perception with basic structure
        perception = PerceptionSnapshot(
            observation={"state": ""},
            grid_hash="test_hash",
        )

        # Create a goal
        goal = ResolvedGoal(
            selected=GoalHypothesis(goal_id="goal1", description="test")
        )

        # Create a violating execution (repeated_falsified=True)
        candidate = PlanCandidate(
            action_id="ACTION1",
            book_id="ACTION1@x,y",
            metadata={"repeated_falsified": True},
        )
        execution = ExecutionResult(
            action_id="ACTION1",
            candidate=candidate,
            observation={},
            did_progress=False,
        )

        # Evaluate
        result = evaluator.evaluate(state, perception, goal, execution)
        evaluation = result.payload

        # Check that compliance_violations was added to metadata
        assert "compliance_violations" in evaluation.metadata
        violations = evaluation.metadata["compliance_violations"]
        assert len(violations) > 0
        assert "ACTION1" in violations[0]

        # Check that existing fields remain unchanged (no new decision logic)
        assert isinstance(evaluation.decision, WorkflowDecision)
        assert isinstance(evaluation.reason, str)
        assert isinstance(evaluation.falsification_delta, int)

    def test_evaluate_with_clean_candidate(self):
        """Evaluator records empty compliance_violations for clean candidates."""
        evaluator = Evaluator()
        state = WorkflowState()

        perception = PerceptionSnapshot(
            observation={"state": ""},
            grid_hash="test_hash",
        )

        goal = ResolvedGoal(
            selected=GoalHypothesis(goal_id="goal1", description="test")
        )

        # Clean execution (repeated_falsified absent or False)
        candidate = PlanCandidate(
            action_id="ACTION1",
            metadata={},
        )
        execution = ExecutionResult(
            action_id="ACTION1",
            candidate=candidate,
            observation={},
            did_progress=False,
        )

        result = evaluator.evaluate(state, perception, goal, execution)
        evaluation = result.payload

        # Should always have compliance_violations key (even if empty)
        assert "compliance_violations" in evaluation.metadata
        assert evaluation.metadata["compliance_violations"] == []


class TestTelemetryIntegration:
    """Test 5: Verify telemetry.py::_step_snapshot includes compliance_violation_count."""

    def test_step_snapshot_clean_evaluation(self):
        """_step_snapshot reflects compliance_violation_count=0 for clean evaluation."""
        telemetry = ArcV2Telemetry(
            task_id="test_task",
            game_id="test_game",
            append_snapshot=None,
        )

        # Create a clean evaluation
        evaluation = EvaluationResult(
            decision=WorkflowDecision.CONTINUE,
            meaningful_progress=False,
            metadata={"compliance_violations": []},
        )
        telemetry._latest_evaluation = evaluation

        # Extract step snapshot
        state = WorkflowState()
        snapshot = telemetry._step_snapshot((state,))

        assert "compliance_violation_count" in snapshot
        assert snapshot["compliance_violation_count"] == 0

    def test_step_snapshot_violating_evaluation(self):
        """_step_snapshot reflects compliance_violation_count for violations."""
        telemetry = ArcV2Telemetry(
            task_id="test_task",
            game_id="test_game",
            append_snapshot=None,
        )

        # Create an evaluation with violations
        violations = ["violation1", "violation2"]
        evaluation = EvaluationResult(
            decision=WorkflowDecision.CONTINUE,
            meaningful_progress=False,
            metadata={"compliance_violations": violations},
        )
        telemetry._latest_evaluation = evaluation

        # Extract step snapshot
        state = WorkflowState()
        snapshot = telemetry._step_snapshot((state,))

        assert "compliance_violation_count" in snapshot
        assert snapshot["compliance_violation_count"] == 2

    def test_step_snapshot_no_evaluation(self):
        """_step_snapshot defaults compliance_violation_count to 0 when no evaluation."""
        telemetry = ArcV2Telemetry(
            task_id="test_task",
            game_id="test_game",
            append_snapshot=None,
        )
        telemetry._latest_evaluation = None

        state = WorkflowState()
        snapshot = telemetry._step_snapshot((state,))

        # Should default to 0 when evaluation is None
        assert "compliance_violation_count" in snapshot
        assert snapshot["compliance_violation_count"] == 0


class TestCheckComplianceViolationsScript:
    """Test 6: Verify scripts/check_compliance_violations.py behavior."""

    def test_clean_trace_file(self):
        """Script exits 0 against a trace file with no violations."""
        import subprocess

        # Create a temporary trace file with no violations
        trace_data = [
            {"step": 1, "compliance_violation_count": 0},
            {"step": 2, "compliance_violation_count": 0},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            trace_path = Path(tmpdir) / "agent_execution_trace.json"
            trace_path.write_text(json.dumps(trace_data))

            # Run the script
            result = subprocess.run(
                ["python", "scripts/check_compliance_violations.py", str(trace_path)],
                cwd=Path(__file__).resolve().parent.parent,
                capture_output=True,
                text=True,
            )

            assert result.returncode == 0
            assert "No compliance violations" in result.stdout

    def test_violating_trace_file(self):
        """Script exits non-zero against a trace file with violations."""
        import subprocess

        # Create a temporary trace file with violations
        trace_data = [
            {"step": 1, "compliance_violation_count": 0},
            {"step": 2, "compliance_violation_count": 1},
            {"step": 3, "compliance_violation_count": 2},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            trace_path = Path(tmpdir) / "agent_execution_trace.json"
            trace_path.write_text(json.dumps(trace_data))

            # Run the script
            result = subprocess.run(
                ["python", "scripts/check_compliance_violations.py", str(trace_path)],
                cwd=Path(__file__).resolve().parent.parent,
                capture_output=True,
                text=True,
            )

            assert result.returncode == 1
            assert "COMPLIANCE VIOLATIONS" in result.stderr
            assert "step 2" in result.stderr
            assert "step 3" in result.stderr

    def test_missing_trace_file(self):
        """Script returns 2 (error exit code) for missing file."""
        import subprocess

        result = subprocess.run(
            ["python", "scripts/check_compliance_violations.py", "/nonexistent/path.json"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 2
        assert "No trace file found" in result.stderr

    def test_auto_discovery_respects_arc_artifacts_dir_env_var(self):
        """The no-argument auto-discovery path must resolve the same
        ARC_ARTIFACTS_DIR override arc_runtime/runner_shell.py itself
        respects -- otherwise the script silently checks the wrong (or a
        stale) trace when a run used a non-default artifacts directory."""
        import os
        import subprocess

        trace_data = [{"step": 1, "compliance_violation_count": 0}]

        with tempfile.TemporaryDirectory() as tmpdir:
            custom_artifacts_dir = Path(tmpdir) / "custom-artifacts"
            custom_artifacts_dir.mkdir()
            (custom_artifacts_dir / "agent_execution_trace.json").write_text(json.dumps(trace_data))

            env = {**os.environ, "ARC_ARTIFACTS_DIR": str(custom_artifacts_dir)}
            result = subprocess.run(
                ["python", "scripts/check_compliance_violations.py"],
                cwd=Path(__file__).resolve().parent.parent,
                capture_output=True,
                text=True,
                env=env,
            )

            assert result.returncode == 0
            assert str(custom_artifacts_dir) in result.stdout
