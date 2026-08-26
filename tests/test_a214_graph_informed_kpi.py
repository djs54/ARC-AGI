"""Tests for A214: `graph_informed_decision_rate`, the complementary near-term
KPI to `graph_grounded_decision_rate` -- counts any candidate with any graph
history (attempts > 0, positive or negative), not just net-positive evidence.
Mirrors tests/test_a196_shift_a_c_trend_telemetry.py's coverage shape for the
sibling `graph_grounded`/`_has_positive_graph_evidence` metric."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.telemetry import ArcV2Telemetry, _has_graph_evidence_at_all
from agents.arc4.types import ExecutionResult, PlanCandidate, WorkflowState

from scripts.graph_compliance_report import report


class TestHasGraphEvidenceAtAll:
    """Unit tests for the standalone helper."""

    def test_fresh_evidence_is_not_informed(self):
        assert _has_graph_evidence_at_all({"attempts": 0, "confidence": 0.0, "contradictions": 0, "supports": 0}) is False

    def test_purely_negative_evidence_is_informed(self):
        """The whole point of this metric: unlike graph_grounded, a purely
        negative history (all contradictions, zero supports) still counts as
        informed -- the graph has SEEN this action before, even though it
        hasn't confirmed it works."""
        assert _has_graph_evidence_at_all({"attempts": 10, "confidence": 0.0, "contradictions": 10, "supports": 0}) is True

    def test_positive_evidence_is_informed(self):
        assert _has_graph_evidence_at_all({"attempts": 5, "confidence": 0.6, "contradictions": 1, "supports": 4}) is True

    def test_empty_mapping_is_not_informed(self):
        assert _has_graph_evidence_at_all({}) is False

    def test_none_is_not_informed(self):
        assert _has_graph_evidence_at_all(None) is False

    def test_list_shape_informed_if_any_item_informed(self):
        assert _has_graph_evidence_at_all([{"attempts": 0}, {"attempts": 3, "contradictions": 3}]) is True

    def test_list_shape_not_informed_if_no_item_informed(self):
        assert _has_graph_evidence_at_all([{"attempts": 0}, {"confidence": 0.0}]) is False

    def test_non_mapping_non_list_is_not_informed(self):
        assert _has_graph_evidence_at_all("not a mapping") is False


class TestStepSnapshotGraphInformedField:
    """graph_informed field on ArcV2Telemetry._step_snapshot."""

    def test_graph_informed_true_for_negative_only_evidence(self):
        telemetry = ArcV2Telemetry(task_id="test_task", game_id="test_game", append_snapshot=None)
        candidate = PlanCandidate(
            action_id="ACTION6",
            metadata={"graph_evidence": {"attempts": 66, "confidence": 0.0, "contradictions": 64, "supports": 0}},
        )
        execution = ExecutionResult(action_id="ACTION6", candidate=candidate, observation={})
        telemetry._latest_execution = execution

        snapshot = telemetry._step_snapshot((WorkflowState(),))

        assert snapshot["graph_informed"] is True
        # Regression guard: graph_grounded must remain False for this same
        # purely-negative shape (this metric must not leak into that one).
        assert snapshot["graph_grounded"] is False

    def test_graph_informed_false_for_fresh_candidate(self):
        telemetry = ArcV2Telemetry(task_id="test_task", game_id="test_game", append_snapshot=None)
        candidate = PlanCandidate(
            action_id="ACTION1",
            metadata={"graph_evidence": {"attempts": 0, "confidence": 0.0, "contradictions": 0, "supports": 0}},
        )
        execution = ExecutionResult(action_id="ACTION1", candidate=candidate, observation={})
        telemetry._latest_execution = execution

        snapshot = telemetry._step_snapshot((WorkflowState(),))

        assert snapshot["graph_informed"] is False
        assert snapshot["graph_grounded"] is False

    def test_graph_informed_true_when_grounded_true(self):
        """A grounded candidate is necessarily also informed -- informed is
        the strictly weaker/broader condition."""
        telemetry = ArcV2Telemetry(task_id="test_task", game_id="test_game", append_snapshot=None)
        candidate = PlanCandidate(
            action_id="ACTION6",
            metadata={"graph_evidence": {"attempts": 5, "confidence": 0.6, "contradictions": 1, "supports": 4}},
        )
        execution = ExecutionResult(action_id="ACTION6", candidate=candidate, observation={})
        telemetry._latest_execution = execution

        snapshot = telemetry._step_snapshot((WorkflowState(),))

        assert snapshot["graph_grounded"] is True
        assert snapshot["graph_informed"] is True

    def test_graph_informed_true_via_entity_neighborhood_grounded(self):
        telemetry = ArcV2Telemetry(task_id="test_task", game_id="test_game", append_snapshot=None)
        candidate = PlanCandidate(action_id="ACTION1", metadata={"entity_neighborhood_grounded": True})
        execution = ExecutionResult(action_id="ACTION1", candidate=candidate, observation={})
        telemetry._latest_execution = execution

        snapshot = telemetry._step_snapshot((WorkflowState(),))

        assert snapshot["graph_informed"] is True

    def test_graph_informed_false_when_no_execution(self):
        telemetry = ArcV2Telemetry(task_id="test_task", game_id="test_game", append_snapshot=None)
        snapshot = telemetry._step_snapshot((WorkflowState(),))
        assert snapshot["graph_informed"] is False


class TestGraphInformedDecisionRateInReport:
    """scripts/graph_compliance_report.py::report()'s new field."""

    def test_informed_rate_counts_negative_only_steps(self):
        steps = [
            {"snapshot_type": "step", "graph_grounded": False, "graph_informed": True},
            {"snapshot_type": "step", "graph_grounded": False, "graph_informed": True},
            {"snapshot_type": "step", "graph_grounded": False, "graph_informed": False},
            {"snapshot_type": "step", "graph_grounded": False, "graph_informed": False},
        ]
        result = report(steps)
        assert result["graph_grounded_decision_rate"] == 0.0
        assert result["graph_informed_decision_rate"] == 50.0

    def test_informed_rate_zero_when_no_evidence_anywhere(self):
        steps = [{"snapshot_type": "step", "graph_grounded": False, "graph_informed": False}] * 3
        result = report(steps)
        assert result["graph_informed_decision_rate"] == 0.0

    def test_informed_rate_100_when_all_informed(self):
        steps = [{"snapshot_type": "step", "graph_grounded": False, "graph_informed": True}] * 3
        result = report(steps)
        assert result["graph_informed_decision_rate"] == 100.0

    def test_grounded_rate_unchanged_by_informed_field_presence(self):
        """Regression guard: adding graph_informed must not perturb the
        existing graph_grounded_decision_rate computation."""
        steps = [
            {"snapshot_type": "step", "graph_grounded": True, "graph_informed": True},
            {"snapshot_type": "step", "graph_grounded": False, "graph_informed": True},
        ]
        result = report(steps)
        assert result["graph_grounded_decision_rate"] == 50.0
        assert result["graph_informed_decision_rate"] == 100.0

    def test_missing_graph_informed_key_defaults_to_not_informed(self):
        """Older trace snapshots (pre-A214) won't have graph_informed at
        all -- report() must not crash on them, and must not count them as
        informed."""
        steps = [{"snapshot_type": "step", "graph_grounded": False}]
        result = report(steps)
        assert result["graph_informed_decision_rate"] == 0.0
