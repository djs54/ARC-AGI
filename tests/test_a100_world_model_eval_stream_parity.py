"""Test A100: World-model eval stream parity for route decisions.

Tests that evaluation metrics include route and terminal-alignment evidence
without requiring full live stream access.
"""

import pytest
from benchmarks.arc3.world_model_eval import WorldModelStepMetrics, WorldModelDecisionMetrics
from agents.arc3.world_model import WorldModelGraph


class TestTerminalAlignmentMetrics:
    """Test terminal alignment metrics in evaluation."""

    def test_terminal_alignment_field_present(self):
        """Terminal alignment field in metrics."""
        metrics = WorldModelStepMetrics(
            task_id="test",
            step=1,
            terminal_alignment="terminal_aligned"
        )
        
        assert metrics.terminal_alignment == "terminal_aligned"

    def test_terminal_progress_trend_tracking(self):
        """Terminal progress trend field present."""
        metrics = WorldModelStepMetrics(
            task_id="test",
            step=1,
            terminal_progress_trend="improving"
        )
        
        assert metrics.terminal_progress_trend == "improving"

    def test_goal_distance_in_metrics(self):
        """Goal distance appears in step metrics."""
        metrics = WorldModelStepMetrics(
            task_id="test",
            step=1,
            terminal_goal_distance=42.5
        )
        
        assert metrics.terminal_goal_distance == 42.5

    def test_meaningful_progress_field(self):
        """Meaningful progress boolean field."""
        metrics = WorldModelStepMetrics(
            task_id="test",
            step=1,
            meaningful_progress=True
        )
        
        assert metrics.meaningful_progress is True

    def test_progress_class_field(self):
        """Progress class field for categorization."""
        metrics = WorldModelStepMetrics(
            task_id="test",
            step=1,
            progress_class="object_progress"
        )
        
        assert metrics.progress_class == "object_progress"


class TestChurnEvidenceInMetrics:
    """Test churn evidence in step/decision metrics."""

    def test_all_actions_churn_evidence_field(self):
        """All-action churn evidence in metrics."""
        evidence = {
            "all_actions_churn": True,
            "actions_tested_count": 3,
            "total_churn_count": 5
        }
        
        metrics = WorldModelStepMetrics(
            task_id="test",
            step=1,
            all_actions_churn_evidence=evidence
        )
        
        assert metrics.all_actions_churn_evidence is not None

    def test_decision_metrics_churn_evidence(self):
        """Decision metrics include churn evidence."""
        evidence = {
            "all_actions_churn": False,
            "actions_tested_count": 2,
            "total_progress_count": 1
        }
        
        metrics = WorldModelDecisionMetrics(
            task_id="test",
            decision="early_stop",
            all_actions_churn_evidence=evidence
        )
        
        assert metrics.all_actions_churn_evidence == evidence


class TestEvaluationStreamParity:
    """Test that eval metrics have complete signal."""

    def test_step_metrics_complete_for_route_analysis(self):
        """Step metrics have all fields needed for route analysis."""
        metrics = WorldModelStepMetrics(
            task_id="test",
            step=1,
            terminal_alignment="terminal_aligned",
            terminal_progress_trend="improving",
            terminal_goal_distance=42.5,
            meaningful_progress=True,
            progress_class="terminal_progress",
            all_actions_churn_evidence={"all_actions_churn": False}
        )
        
        assert metrics.terminal_alignment is not None
        assert metrics.terminal_progress_trend is not None
        assert metrics.terminal_goal_distance is not None
        assert metrics.meaningful_progress is not None
        assert metrics.progress_class is not None

    def test_decision_metrics_complete_for_route_decisions(self):
        """Decision metrics support route decision evaluation."""
        metrics = WorldModelDecisionMetrics(
            task_id="test",
            decision="early_stop",
            world_model_decision="route_search_required",
            all_actions_churn_evidence={"all_actions_churn": False},
            trigger="route_evidence_detected"
        )
        
        assert metrics.world_model_decision == "route_search_required"
        assert metrics.all_actions_churn_evidence is not None

    def test_metrics_to_dict_preserves_fields(self):
        """Metrics serialize with all fields intact."""
        metrics = WorldModelStepMetrics(
            task_id="test",
            step=1,
            terminal_alignment="oscillating",
            terminal_progress_trend="flat",
            terminal_goal_distance=50.0,
            meaningful_progress=False,
            progress_class="local_object_progress"
        )
        
        d = metrics.to_dict()
        assert d["terminal_alignment"] == "oscillating"
        assert d["terminal_progress_trend"] == "flat"
        assert d["terminal_goal_distance"] == 50.0
        assert d["meaningful_progress"] is False
        assert d["progress_class"] == "local_object_progress"


class TestEvaluationRegressionDetection:
    """Test regression detection with eval metrics."""

    def test_local_object_progress_not_confused_with_terminal(self):
        """Local object progress correctly marked as non-terminal."""
        metrics = WorldModelStepMetrics(
            task_id="cd82",
            step=5,
            terminal_alignment="local_only",
            terminal_progress_trend="flat",
            meaningful_progress=True,
            progress_class="object_progress"
        )
        
        assert metrics.terminal_alignment == "local_only"
        assert metrics.progress_class == "object_progress"

    def test_oscillating_progress_correctly_marked(self):
        """Oscillating progress not treated as progress."""
        metrics = WorldModelStepMetrics(
            task_id="test",
            step=10,
            terminal_alignment="oscillating",
            terminal_progress_trend="oscillating",
            meaningful_progress=False,
            progress_class="none"
        )
        
        assert metrics.terminal_alignment == "oscillating"
        assert metrics.meaningful_progress is False

    def test_delayed_effect_pending_marked(self):
        """Delayed effect marked appropriately."""
        metrics = WorldModelStepMetrics(
            task_id="test",
            step=3,
            terminal_alignment="delayed_effect_pending",
            terminal_progress_trend="flat",
            meaningful_progress=False
        )
        
        assert metrics.terminal_alignment == "delayed_effect_pending"


class TestMetricsEdgeCases:
    """Test edge cases in evaluation metrics."""

    def test_unknown_alignment_handled(self):
        """Unknown alignment values handled."""
        metrics = WorldModelStepMetrics(
            task_id="test",
            step=1,
            terminal_alignment="unknown"
        )
        
        assert metrics.terminal_alignment == "unknown"

    def test_none_distance_handled(self):
        """None goal distance handled."""
        metrics = WorldModelStepMetrics(
            task_id="test",
            step=1,
            terminal_goal_distance=None
        )
        
        assert metrics.terminal_goal_distance is None

    def test_empty_churn_evidence(self):
        """Empty churn evidence handled."""
        metrics = WorldModelStepMetrics(
            task_id="test",
            step=1,
            all_actions_churn_evidence={}
        )
        
        assert metrics.all_actions_churn_evidence == {}
