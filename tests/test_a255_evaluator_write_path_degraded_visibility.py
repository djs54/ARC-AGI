"""Tests for A255: evaluator.py's three graph-*write* methods
(_record_transition, _record_rule_evidence, _record_evaluation) silently
swallow exceptions without setting self._degraded, unlike the two graph
*read* sites (fetch_causal_path, fetch_untested_actions) A244 already fixed
in this same file. Confirmed live: a peer session cross-referenced hippocampy's
own server-side logs and found record_rule_evidence genuinely raised
RuntimeError three times during a real ARC_AGI smoke run, while
state.evaluate_degraded stayed false across all 515 telemetry snapshots of
that run. See backlog/A255.md.

This extends A244's exact pattern (except Exception: self._degraded = True)
to the three remaining unguarded sites. Fallback *behavior* is unchanged --
"failed" is still returned/stored in evaluation.metadata exactly as before;
only visibility is added.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.evaluator import EvaluationLimits, Evaluator
from agents.arc4.types import (
    ExecutionResult,
    GoalHypothesis,
    PerceptionSnapshot,
    PlanCandidate,
    ResolvedGoal,
    WorkflowState,
)


# --- Shared fixtures --------------------------------------------------------


def _goal(goal_id: str = "goal-1") -> ResolvedGoal:
    return ResolvedGoal(selected=GoalHypothesis(goal_id=goal_id, description=goal_id, confidence=0.8))


def _perception() -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"grid": [[1]]},
        grid_hash="grid-1",
        metadata={"grid_diff": {"changed_cells": [[0, 0]]}},
    )


def _execution(
    action_id: str = "move-right",
    *,
    predicted_effect: str | None = "shift",
    actual_effect: str | None = "shift",
    did_progress: bool = False,
    metadata: dict | None = None,
) -> ExecutionResult:
    candidate = PlanCandidate(action_id=action_id, expected_effect=predicted_effect)
    return ExecutionResult(
        action_id=action_id,
        candidate=candidate,
        observation={"grid": [[1]]},
        did_progress=did_progress,
        predicted_effect=predicted_effect,
        actual_effect=actual_effect,
        metadata=metadata or {},
    )


class _HealthyGraphPort:
    """Every graph-write call evaluate() touches, all succeeding normally --
    the regression baseline these tests contrast against. Also implements
    the read-path methods A244 already covers, healthy, so those sites don't
    interfere with these write-path assertions."""

    def fetch_causal_path(self, action_id: str) -> dict[str, Any]:
        return {"path_exists": False}

    def fetch_untested_actions(self) -> list[str]:
        return ["ACTION2"]

    def record_transition(self, execution: Any, grid_diff: Any, entities: Any) -> dict[str, Any]:
        return {"status": "ok"}

    def record_rule_evidence(self, execution: Any, grid_diff: Any, entities: Any) -> dict[str, Any]:
        return {"status": "ok"}

    def record_evaluation(self, evaluation: Any) -> dict[str, Any]:
        return {"status": "ok"}


# --- _record_transition's except site ---------------------------------------


class TestRecordTransitionDegradedVisibility:
    def test_raising_record_transition_sets_degraded_true_return_value_unchanged(self):
        class _Port(_HealthyGraphPort):
            def record_transition(self, execution: Any, grid_diff: Any, entities: Any) -> dict[str, Any]:
                raise RuntimeError("hippocampy MCP not available")

        evaluator = Evaluator(graph_query_port=_Port())
        result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), _execution(did_progress=True))

        assert result.payload is not None
        assert result.payload.degraded is True
        assert result.payload.metadata["transition_recording"] == "failed"

    def test_healthy_record_transition_leaves_degraded_false(self):
        evaluator = Evaluator(graph_query_port=_HealthyGraphPort())
        result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), _execution(did_progress=True))

        assert result.payload is not None
        assert result.payload.degraded is False
        assert result.payload.metadata["transition_recording"] == "ok"


# --- _record_rule_evidence's except site ------------------------------------


class TestRecordRuleEvidenceDegradedVisibility:
    def test_raising_record_rule_evidence_sets_degraded_true_return_value_unchanged(self):
        """This is the exact method a peer session confirmed genuinely
        raised RuntimeError server-side during a real live-smoke run, three
        times, while evaluate_degraded stayed false throughout."""

        class _Port(_HealthyGraphPort):
            def record_rule_evidence(self, execution: Any, grid_diff: Any, entities: Any) -> dict[str, Any]:
                raise RuntimeError("hippocampy MCP not available")

        evaluator = Evaluator(graph_query_port=_Port())
        result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), _execution(did_progress=True))

        assert result.payload is not None
        assert result.payload.degraded is True
        assert result.payload.metadata["rule_recording"] == "failed"

    def test_healthy_record_rule_evidence_leaves_degraded_false(self):
        evaluator = Evaluator(graph_query_port=_HealthyGraphPort())
        result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), _execution(did_progress=True))

        assert result.payload is not None
        assert result.payload.degraded is False
        assert result.payload.metadata["rule_recording"] == "ok"


# --- _record_evaluation's except site ---------------------------------------


class TestRecordEvaluationDegradedVisibility:
    def test_raising_record_evaluation_sets_degraded_true_return_value_unchanged(self):
        class _Port(_HealthyGraphPort):
            def record_evaluation(self, evaluation: Any) -> dict[str, Any]:
                raise RuntimeError("hippocampy MCP not available")

        evaluator = Evaluator(graph_query_port=_Port())
        result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), _execution(did_progress=True))

        assert result.payload is not None
        assert result.payload.degraded is True
        assert result.payload.metadata["graph_recording"] == "failed"

    def test_healthy_record_evaluation_leaves_degraded_false(self):
        evaluator = Evaluator(graph_query_port=_HealthyGraphPort())
        result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), _execution(did_progress=True))

        assert result.payload is not None
        assert result.payload.degraded is False
        assert result.payload.metadata["graph_recording"] == "ok"


# --- Regression: all three healthy, no port, and multi-site accumulation ----


class TestRegressionAndAccumulation:
    def test_no_graph_port_leaves_degraded_false(self):
        evaluator = Evaluator(graph_query_port=None)
        result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), _execution(did_progress=True))

        assert result.payload is not None
        assert result.payload.degraded is False
        assert result.payload.metadata["transition_recording"] == "skipped"
        assert result.payload.metadata["rule_recording"] == "skipped"
        assert result.payload.metadata["graph_recording"] == "skipped"

    def test_all_three_write_sites_healthy_leaves_degraded_false(self):
        evaluator = Evaluator(graph_query_port=_HealthyGraphPort())
        result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), _execution(did_progress=True))

        assert result.payload is not None
        assert result.payload.degraded is False

    def test_degraded_accumulates_across_multiple_write_sites_within_one_evaluate_call(self):
        """Mirrors A244's own multi-site accumulation verification -- now
        three potential write sites (plus the two pre-existing read sites),
        not two. self._degraded must end up True when more than one site
        raises within the same evaluate() call."""

        class _Port(_HealthyGraphPort):
            def record_transition(self, execution: Any, grid_diff: Any, entities: Any) -> dict[str, Any]:
                raise RuntimeError("hippocampy MCP not available")

            def record_rule_evidence(self, execution: Any, grid_diff: Any, entities: Any) -> dict[str, Any]:
                raise RuntimeError("hippocampy MCP not available")

        evaluator = Evaluator(graph_query_port=_Port())
        result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), _execution(did_progress=True))

        assert result.payload.degraded is True
        assert result.payload.metadata["transition_recording"] == "failed"
        assert result.payload.metadata["rule_recording"] == "failed"

    def test_degraded_does_not_leak_across_successive_evaluate_calls(self):
        """Same reset-per-cycle guarantee A244 verified for the read sites,
        now confirmed for the write sites too."""

        class _RaisingPort(_HealthyGraphPort):
            def record_rule_evidence(self, execution: Any, grid_diff: Any, entities: Any) -> dict[str, Any]:
                raise RuntimeError("hippocampy MCP not available")

        evaluator = Evaluator(graph_query_port=_RaisingPort())
        degraded_result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), _execution(did_progress=True))

        evaluator._graph_query_port = _HealthyGraphPort()
        healthy_result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), _execution(did_progress=True))

        assert degraded_result.payload.degraded is True
        assert healthy_result.payload.degraded is False

    def test_read_and_write_site_degradation_both_reflected_in_final_result(self):
        """The read-path sites (A244) set self._degraded before evaluation
        is constructed; the write-path sites (A255) run after. Both must
        still be visible in the final EvaluationResult.degraded -- proving
        the post-construction sync, not just the read-path snapshot, is what
        the returned result actually carries."""

        class _Port(_HealthyGraphPort):
            def fetch_causal_path(self, action_id: str) -> dict[str, Any]:
                raise RuntimeError("hippocampy MCP not available")

        evaluator = Evaluator(graph_query_port=_Port())
        result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), _execution(did_progress=True))

        assert result.payload.degraded is True

    def test_healthy_graph_port_regression_matches_a244_baseline(self):
        """Existing A244 regression shape: a fully healthy port (including
        the write-path methods this card adds) still yields degraded=False
        end to end."""
        evaluator = Evaluator(graph_query_port=_HealthyGraphPort(), limits=EvaluationLimits())
        result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), _execution(did_progress=True))

        assert result.payload.degraded is False
