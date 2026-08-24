"""Tests for A194: `_action_space_exhausted` consults the graph before terminating
on flat attempt-count threshold alone. When the graph reports untested actions remain,
the episode should not terminate on this signal alone. Degradation path (graph unavailable
or raises exception) reverts to flat threshold behavior byte-for-byte."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.evaluator import EvaluationLimits, Evaluator
from agents.arc4.types import ExecutionResult, GoalHypothesis, PerceptionSnapshot, PlanCandidate, ResolvedGoal, WorkflowState


def _state() -> WorkflowState:
    return WorkflowState(
        step_index=0,
        action_attempt_counts={},
        action_falsification_counts={},
        consecutive_no_progress_count=0,
    )


def _perception() -> PerceptionSnapshot:
    return PerceptionSnapshot(observation={"grid": "hash-1"}, grid_hash="hash-1")


def _goal() -> ResolvedGoal:
    return ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="test goal", confidence=0.8))


def _execution(action_id: str = "ACTION1", metadata: dict[str, Any] | None = None) -> ExecutionResult:
    if metadata is None:
        metadata = {}
    return ExecutionResult(
        action_id=action_id,
        candidate=PlanCandidate(action_id=action_id, goal_id="goal-1"),
        observation={"grid": "new-hash"},
        did_progress=True,
        metadata=metadata,
    )


class TestActionSpaceExhaustedDirectly:
    """Test _action_space_exhausted method directly (no evaluate() call)."""

    def test_graph_returns_untested_actions_prevents_termination_at_threshold(self):
        """Scenario 1: Graph reports untested actions remain — don't terminate."""
        graph = Mock()
        graph.fetch_untested_actions = Mock(return_value=["ACTION2", "ACTION3"])
        evaluator = Evaluator(graph_query_port=graph, limits=EvaluationLimits(exhausted_action_attempt_threshold=4))

        exhausted, source = evaluator._action_space_exhausted(_execution(), current_attempt_count=5)

        assert exhausted is False
        assert source == ""
        assert graph.fetch_untested_actions.call_count == 1

    def test_graph_returns_empty_confirms_exhaustion_at_threshold(self):
        """Scenario 2: Graph confirms no untested actions — do terminate."""
        graph = Mock()
        graph.fetch_untested_actions = Mock(return_value=[])
        evaluator = Evaluator(graph_query_port=graph, limits=EvaluationLimits(exhausted_action_attempt_threshold=4))

        exhausted, source = evaluator._action_space_exhausted(_execution(), current_attempt_count=5)

        assert exhausted is True
        assert source == "graph_confirmed_no_untested"
        assert graph.fetch_untested_actions.call_count == 1

    def test_no_graph_port_falls_back_to_threshold_only(self):
        """Scenario 3: No graph port — fall back to threshold-only behavior."""
        evaluator = Evaluator(graph_query_port=None, limits=EvaluationLimits(exhausted_action_attempt_threshold=4))

        exhausted, source = evaluator._action_space_exhausted(_execution(), current_attempt_count=5)

        assert exhausted is True
        assert source == "threshold_only"

    def test_graph_port_without_fetch_untested_actions_falls_back(self):
        """Scenario 4: Graph port lacks fetch_untested_actions — degrade gracefully."""
        graph = Mock()
        # Explicitly remove the method, not just return None
        delattr(graph, "fetch_untested_actions")
        evaluator = Evaluator(graph_query_port=graph, limits=EvaluationLimits(exhausted_action_attempt_threshold=4))

        exhausted, source = evaluator._action_space_exhausted(_execution(), current_attempt_count=5)

        assert exhausted is True
        assert source == "threshold_only"

    def test_graph_fetch_raises_exception_falls_back(self):
        """Scenario 5: Graph fetch raises exception — catch and degrade."""
        graph = Mock()
        graph.fetch_untested_actions = Mock(side_effect=RuntimeError("graph unavailable"))
        evaluator = Evaluator(graph_query_port=graph, limits=EvaluationLimits(exhausted_action_attempt_threshold=4))

        exhausted, source = evaluator._action_space_exhausted(_execution(), current_attempt_count=5)

        assert exhausted is True
        assert source == "threshold_only"

    def test_env_reported_flag_short_circuits_graph_check(self):
        """Scenario 6: Env-reported flag takes priority, graph never consulted."""
        graph = Mock()
        graph.fetch_untested_actions = Mock(return_value=["ACTION2"])
        evaluator = Evaluator(graph_query_port=graph, limits=EvaluationLimits(exhausted_action_attempt_threshold=4))

        execution = _execution(metadata={"action_space_exhausted": True})
        exhausted, source = evaluator._action_space_exhausted(execution, current_attempt_count=5)

        assert exhausted is True
        assert source == "env_reported"
        # Critical: graph must not be called when env flag is set
        assert graph.fetch_untested_actions.call_count == 0

    def test_env_reported_flag_exhausted_action_space_variant(self):
        """Scenario 6b: Alternative env-reported flag key also short-circuits."""
        graph = Mock()
        graph.fetch_untested_actions = Mock(return_value=["ACTION2"])
        evaluator = Evaluator(graph_query_port=graph, limits=EvaluationLimits(exhausted_action_attempt_threshold=4))

        execution = _execution(metadata={"exhausted_action_space": True})
        exhausted, source = evaluator._action_space_exhausted(execution, current_attempt_count=5)

        assert exhausted is True
        assert source == "env_reported"
        # Graph must not be called
        assert graph.fetch_untested_actions.call_count == 0

    def test_attempt_count_below_threshold_skips_graph_check(self):
        """Scenario 7: Below threshold, don't consult graph at all."""
        graph = Mock()
        graph.fetch_untested_actions = Mock(return_value=["ACTION2"])
        evaluator = Evaluator(graph_query_port=graph, limits=EvaluationLimits(exhausted_action_attempt_threshold=4))

        exhausted, source = evaluator._action_space_exhausted(_execution(), current_attempt_count=2)

        assert exhausted is False
        assert source == ""
        # Critical: graph must not be called when under threshold
        assert graph.fetch_untested_actions.call_count == 0

    def test_at_exact_threshold_boundary(self):
        """Test at exact threshold boundary (== threshold)."""
        graph = Mock()
        graph.fetch_untested_actions = Mock(return_value=[])
        evaluator = Evaluator(graph_query_port=graph, limits=EvaluationLimits(exhausted_action_attempt_threshold=4))

        exhausted, source = evaluator._action_space_exhausted(_execution(), current_attempt_count=4)

        assert exhausted is True
        assert source == "graph_confirmed_no_untested"
        assert graph.fetch_untested_actions.call_count == 1


class TestExhaustionSourceInMetadata:
    """Test exhaustion_source appears in metadata only when exhausted is True."""

    def test_env_reported_source_in_metadata(self):
        """Scenario 8a: env_reported termination includes exhaustion_source."""
        graph = Mock()
        graph.fetch_untested_actions = Mock(return_value=["ACTION2"])
        evaluator = Evaluator(graph_query_port=graph)

        execution = _execution(metadata={"action_space_exhausted": True})
        result = evaluator.evaluate(_state(), _perception(), _goal(), execution)

        assert result.payload.metadata["action_space_exhausted"] is True
        assert result.payload.metadata["exhaustion_source"] == "env_reported"

    def test_graph_confirmed_source_in_metadata(self):
        """Scenario 8b: graph_confirmed_no_untested termination includes source."""
        state = _state()
        state.action_attempt_counts["ACTION1"] = 5
        graph = Mock()
        graph.fetch_untested_actions = Mock(return_value=[])
        evaluator = Evaluator(graph_query_port=graph, limits=EvaluationLimits(exhausted_action_attempt_threshold=4))

        result = evaluator.evaluate(state, _perception(), _goal(), _execution())

        assert result.payload.metadata["action_space_exhausted"] is True
        assert result.payload.metadata["exhaustion_source"] == "graph_confirmed_no_untested"

    def test_threshold_only_source_in_metadata(self):
        """Scenario 8c: threshold_only termination includes source."""
        state = _state()
        state.action_attempt_counts["ACTION1"] = 5
        evaluator = Evaluator(graph_query_port=None, limits=EvaluationLimits(exhausted_action_attempt_threshold=4))

        result = evaluator.evaluate(state, _perception(), _goal(), _execution())

        assert result.payload.metadata["action_space_exhausted"] is True
        assert result.payload.metadata["exhaustion_source"] == "threshold_only"

    def test_not_exhausted_has_no_exhaustion_source(self):
        """Scenario 8d: when not exhausted, exhaustion_source key must be absent."""
        evaluator = Evaluator(graph_query_port=None, limits=EvaluationLimits(exhausted_action_attempt_threshold=4))

        result = evaluator.evaluate(_state(), _perception(), _goal(), _execution())

        assert result.payload.metadata["action_space_exhausted"] is False
        assert "exhaustion_source" not in result.payload.metadata

    def test_not_exhausted_with_graph_has_no_exhaustion_source(self):
        """When not exhausted even with graph available, source key absent."""
        graph = Mock()
        graph.fetch_untested_actions = Mock(return_value=["ACTION2", "ACTION3"])
        state = _state()
        state.action_attempt_counts["ACTION1"] = 5
        evaluator = Evaluator(graph_query_port=graph, limits=EvaluationLimits(exhausted_action_attempt_threshold=4))

        result = evaluator.evaluate(state, _perception(), _goal(), _execution())

        assert result.payload.metadata["action_space_exhausted"] is False
        assert "exhaustion_source" not in result.payload.metadata


class TestDegradationAndRegressionGuards:
    """Ensure clean degradation and no regressions in existing behavior."""

    def test_graph_port_none_does_not_crash(self):
        """Regression: None graph port must not crash."""
        evaluator = Evaluator(graph_query_port=None, limits=EvaluationLimits(exhausted_action_attempt_threshold=4))
        result = evaluator._action_space_exhausted(_execution(), current_attempt_count=5)
        assert result == (True, "threshold_only")

    def test_graph_port_with_no_method_does_not_crash(self):
        """Regression: missing method must not crash."""
        graph = Mock(spec=[])  # spec=[] means no methods at all
        evaluator = Evaluator(graph_query_port=graph, limits=EvaluationLimits(exhausted_action_attempt_threshold=4))
        result = evaluator._action_space_exhausted(_execution(), current_attempt_count=5)
        assert result == (True, "threshold_only")

    def test_threshold_default_unchanged(self):
        """The default threshold remains 4, not changed by this card."""
        limits = EvaluationLimits()
        assert limits.exhausted_action_attempt_threshold == 4

    def test_under_threshold_behavior_unchanged(self):
        """Under-threshold behavior is identical to before (early return, no graph call)."""
        graph = Mock()
        graph.fetch_untested_actions = Mock(return_value=["ACTION2"])
        evaluator = Evaluator(graph_query_port=graph, limits=EvaluationLimits(exhausted_action_attempt_threshold=4))

        # Attempt count 2 is < 4 (default threshold)
        exhausted, source = evaluator._action_space_exhausted(_execution(), current_attempt_count=2)

        assert exhausted is False
        assert source == ""
        # Most critical: graph must NOT be called when under threshold
        assert graph.fetch_untested_actions.call_count == 0
