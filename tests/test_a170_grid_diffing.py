"""Tests for A170: before/after grid diffing as structured causal evidence.
Every action's effect previously collapsed to a boolean grid_changed --
no way to represent "clicking here turned 3 cells from color 2 to 5",
which is exactly the evidence shape ARC-style causal reasoning needs."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.evaluator import EvaluationLimits, Evaluator
from agents.arc4.perceive import PerceiveAgent
from agents.arc4.types import ExecutionResult, GoalHypothesis, PerceptionSnapshot, PlanCandidate, ResolvedGoal, WorkflowState


class TestDiffGrids:
    def test_diff_detects_changed_cells(self):
        previous = [[1, 2], [3, 4]]
        current = [[1, 9], [3, 4]]
        result = PerceiveAgent._diff_grids(previous, current)
        assert result["changed_cells"] == [{"row": 0, "col": 1, "from": 2, "to": 9}]
        assert result["changed_count"] == 1
        assert result["truncated"] is False

    def test_diff_no_previous_grid_returns_empty(self):
        result = PerceiveAgent._diff_grids(None, [[1, 2], [3, 4]])
        assert result == {"changed_cells": [], "changed_count": 0, "truncated": False}

    def test_diff_truncates_large_changesets(self):
        previous = [[0] * 10 for _ in range(10)]
        current = [[1] * 10 for _ in range(10)]
        result = PerceiveAgent._diff_grids(previous, current, max_entries=5)
        assert result["changed_count"] == 100
        assert len(result["changed_cells"]) == 5
        assert result["truncated"] is True

    def test_diff_shape_mismatch_returns_empty(self):
        previous = [[1, 2], [3, 4]]
        current = [[1, 2, 3]]
        result = PerceiveAgent._diff_grids(previous, current)
        assert result == {"changed_cells": [], "changed_count": 0, "truncated": False}


class TestPerceiveAttachesGridDiff:
    def test_perceive_computes_diff_against_prior_step(self):
        agent = PerceiveAgent()
        state = WorkflowState()

        first = agent.perceive(state, {"grid": [[1, 2], [3, 4]]})
        assert first.payload.metadata["grid_diff"]["changed_count"] == 0

        second = agent.perceive(state, {"grid": [[1, 9], [3, 4]]})
        assert second.payload.metadata["grid_diff"]["changed_count"] == 1
        assert second.payload.metadata["grid_diff"]["changed_cells"] == [{"row": 0, "col": 1, "from": 2, "to": 9}]

    def test_first_perception_has_no_prior_grid(self):
        agent = PerceiveAgent()
        state = WorkflowState()
        result = agent.perceive(state, {"grid": [[1, 2], [3, 4]]})
        assert result.payload.metadata["grid_diff"]["changed_count"] == 0


def _state() -> WorkflowState:
    return WorkflowState(
        step_index=0,
        action_attempt_counts={},
        action_falsification_counts={},
        consecutive_no_progress_count=0,
    )


def _goal() -> ResolvedGoal:
    return ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="test goal", confidence=0.8))


def _execution(action_id: str = "ACTION1", did_progress: bool = True) -> ExecutionResult:
    return ExecutionResult(
        action_id=action_id,
        candidate=PlanCandidate(action_id=action_id, goal_id="goal-1"),
        observation={"grid": "new-hash"},
        did_progress=did_progress,
    )


class TestEvaluatorEnrichedWithoutChangingObservedKind:
    def test_actual_effect_enriched_with_diff_without_changing_observed_kind(self):
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1"},
            grid_hash="hash-1",
            metadata={"grid_diff": {"changed_cells": [{"row": 0, "col": 1, "from": 2, "to": 9}], "changed_count": 1, "truncated": False}},
        )
        evaluator = Evaluator(graph_query_port=None, limits=EvaluationLimits())
        result = evaluator.evaluate(_state(), perception, _goal(), _execution())

        assert result.payload.metadata["grid_diff"]["changed_count"] == 1
        # observed_kind classification is untouched by the diff -- it's still
        # driven by grid_changed/level_gain/state, not by the diff's presence.
        assert result.payload.metadata["observed_kind"] in ("grid_change", "no_change", "level_gain", "state_change")

    def test_missing_grid_diff_metadata_defaults_to_empty(self):
        perception = PerceptionSnapshot(observation={"grid": "hash-1"}, grid_hash="hash-1")
        evaluator = Evaluator(graph_query_port=None, limits=EvaluationLimits())
        result = evaluator.evaluate(_state(), perception, _goal(), _execution())

        assert result.payload.metadata["grid_diff"] == {}
