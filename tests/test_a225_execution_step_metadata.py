"""A225: Executor must stamp the real step index onto ExecutionResult.metadata
so ArcGraphQueryPort._execution_step (agents/arc4/graph_queries.py) can find
it -- confirmed via live graph query that this has been silently defaulting
to 0 for every execution since A176, collapsing repeated-attempt history at
the same entity+action into a single overwritten graph node."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.executor import Executor
from agents.arc4.types import PlanCandidate, WorkflowState


class _FakeTransport:
    def execute_action(self, action_id, action_args, context):
        return {"state": "NOT_FINISHED", "grid": [[0]]}


class TestExecutionStepMetadata:
    def test_success_path_stamps_real_step_from_game_context(self):
        executor = Executor(transport=_FakeTransport())
        plan = PlanCandidate(action_id="ACTION6", goal_id="g1", payload={"x": 1, "y": 1})
        state = WorkflowState()
        game_context = {"game_id": "g", "step": 7, "session_id": "s"}

        result = executor.execute(state, plan, game_context)

        assert result.payload.metadata["step"] == 7

    def test_failure_path_stamps_real_step_from_game_context(self):
        executor = Executor(transport=None)  # forces the missing-transport failure path
        plan = PlanCandidate(action_id="ACTION6", goal_id="g1", payload={"x": 1, "y": 1})
        state = WorkflowState()
        game_context = {"game_id": "g", "step": 3, "session_id": "s"}

        result = executor.execute(state, plan, game_context)

        assert result.payload.metadata["step"] == 3

    def test_missing_step_in_game_context_stays_none_not_crash(self):
        """No `step` key at all (e.g. a caller that doesn't set one) must not
        raise -- ArcGraphQueryPort._execution_step already defaults safely to
        0 for a None/missing value, so Executor just needs to pass through
        whatever's there without assuming it exists."""
        executor = Executor(transport=_FakeTransport())
        plan = PlanCandidate(action_id="ACTION6", goal_id="g1", payload={"x": 1, "y": 1})
        state = WorkflowState()
        game_context = {"game_id": "g", "session_id": "s"}

        result = executor.execute(state, plan, game_context)

        assert result.payload.metadata["step"] is None
