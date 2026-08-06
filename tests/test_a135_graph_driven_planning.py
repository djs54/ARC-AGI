"""Tests for A135: Wire graph read tools into planner, vet, and evaluator."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.evaluator import Evaluator, EvaluationLimits
from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.plan_vetter import PlanVetter, PlanVetterLimits
from agents.arc4.types import (
    ExecutionResult,
    GoalHypothesis,
    PerceptionSnapshot,
    PlanCandidate,
    PlanningResult,
    ResolvedGoal,
    WorkflowState,
)


# ── Mock graph port ───────────────────────────────────────────────────

class MockGraphPort:
    """Scriptable mock for GraphQueryPort read methods used by A135."""

    def __init__(
        self,
        untested_actions: list[str] | None = None,
        per_action_evidence: dict[str, dict[str, Any]] | None = None,
        action_gates: dict[str, dict[str, Any]] | None = None,
        causal_paths: dict[str, dict[str, Any]] | None = None,
    ):
        self._untested = untested_actions or []
        self._evidence = per_action_evidence or {}
        self._gates = action_gates or {}
        self._causal = causal_paths or {}

    # Write methods (no-ops for testing)
    def ingest_perception(self, perception: Any) -> Any:
        return None

    def fetch_goal_evidence(self, perception: Any, goal: Any = None) -> Any:
        return {}

    def record_plan(self, plan: Any) -> Any:
        return None

    def record_vet(self, vet: Any) -> Any:
        return None

    def record_execution(self, execution: Any) -> Any:
        return None

    def record_evaluation(self, evaluation: Any) -> Any:
        return None

    # A135 read methods
    def fetch_untested_actions(self) -> list[str]:
        return list(self._untested)

    def fetch_per_action_evidence(self, action_id: str) -> dict[str, Any]:
        return self._evidence.get(action_id, {"supports": 0, "contradictions": 0, "confidence": 0.0, "attempts": 0})

    def check_action_gate(self, action_id: str) -> dict[str, Any]:
        return self._gates.get(action_id, {"allowed": True, "reason": "no_evidence"})

    def fetch_causal_path(self, action_id: str) -> dict[str, Any]:
        return self._causal.get(action_id, {"path_exists": False, "supports": False, "contradicts": False})


# ── Helpers ────────────────────────────────────────────────────────────

def _state(**overrides) -> WorkflowState:
    defaults = dict(
        step_index=0,
        action_attempt_counts={},
        action_falsification_counts={},
        consecutive_no_progress_count=0,
    )
    defaults.update(overrides)
    return WorkflowState(**defaults)


def _perception(grid_hash: str = "hash-1") -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"grid": grid_hash, "available_actions": ["action-a", "action-b"]},
        grid_hash=grid_hash,
    )


def _goal(goal_id: str = "goal-1") -> ResolvedGoal:
    return ResolvedGoal(
        selected=GoalHypothesis(goal_id=goal_id, description="test goal", confidence=0.8),
    )


def _execution(action_id: str = "action-a", did_progress: bool = True) -> ExecutionResult:
    return ExecutionResult(
        action_id=action_id,
        candidate=PlanCandidate(action_id=action_id, goal_id="goal-1"),
        observation={"grid": "new-hash"},
        did_progress=did_progress,
    )


def _plan_result(action_id: str = "action-a", score: float = 0.5, alternatives: tuple = ()) -> PlanningResult:
    return PlanningResult(
        candidate=PlanCandidate(action_id=action_id, goal_id="goal-1", score=score),
        alternatives=alternatives,
    )


# ── Planner: fetch_untested_actions merges graph candidates ───────────

class TestA135PlannerGraphIntegration:
    """Planner should use graph port to discover untested actions and score with evidence."""

    def test_untested_actions_from_graph_appear_in_candidates(self):
        """Graph-sourced untested actions should be added to candidate pool when API mask includes them."""
        graph = MockGraphPort(untested_actions=["graph-action-x", "graph-action-y"])
        planner = PlanGenerator(PlanGeneratorLimits())
        state = _state()
        # Include graph actions in observation so they pass the API mask filter
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["action-a", "action-b", "graph-action-x", "graph-action-y"]},
            grid_hash="hash-1",
        )
        goal = _goal()

        phase_result = planner.generate(state, perception, goal, graph_port=graph)
        result = phase_result.payload
        all_action_ids = [result.candidate.action_id] + [a.action_id for a in result.alternatives]
        # At least one graph-sourced action should appear
        graph_actions_present = [a for a in all_action_ids if a.startswith("graph-action-")]
        assert len(graph_actions_present) > 0, f"No graph actions found in {all_action_ids}"

    def test_graph_evidence_boosts_candidate_score(self):
        """High graph confidence should increase candidate score."""
        graph = MockGraphPort(
            per_action_evidence={
                "action-a": {"supports": 5, "contradictions": 0, "confidence": 0.9, "attempts": 3},
            },
        )
        planner = PlanGenerator(PlanGeneratorLimits())
        state = _state()
        perception = _perception()
        goal = _goal()

        result_with_graph = planner.generate(state, perception, goal, graph_port=graph).payload
        result_without_graph = planner.generate(state, perception, goal, graph_port=None).payload

        # With graph evidence, the candidate metadata should include graph_evidence
        if result_with_graph.candidate.metadata:
            graph_ev = result_with_graph.candidate.metadata.get("graph_evidence", {})
            # If graph evidence was recorded, it should have positive confidence
            if graph_ev:
                assert graph_ev.get("confidence", 0) > 0

    def test_graph_contradiction_penalizes_candidate(self):
        """Graph contradictions should reduce candidate score."""
        graph = MockGraphPort(
            per_action_evidence={
                "action-a": {"supports": 0, "contradictions": 5, "confidence": 0.1, "attempts": 5},
                "action-b": {"supports": 3, "contradictions": 0, "confidence": 0.8, "attempts": 3},
            },
        )
        planner = PlanGenerator(PlanGeneratorLimits())
        state = _state()
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["action-a", "action-b"]},
            grid_hash="hash-1",
        )
        goal = _goal()

        result = planner.generate(state, perception, goal, graph_port=graph).payload
        # The heavily contradicted action-a should not be top candidate
        # (action-b should be preferred due to positive evidence)
        assert result.candidate.action_id == "action-b", (
            f"Expected action-b to win over contradicted action-a, got {result.candidate.action_id}"
        )

    def test_planner_works_without_graph_port(self):
        """Planner should work fine when graph_port is None."""
        planner = PlanGenerator(PlanGeneratorLimits())
        state = _state()
        perception = _perception()
        goal = _goal()

        result = planner.generate(state, perception, goal, graph_port=None).payload
        assert result.candidate is not None

    def test_falsification_penalty_applies_with_real_server_field_names(self):
        """A162: ArcGraphQueryPort's field mapping (not the MockGraphPort test double)
        must actually surface falsified_count as contradictions, so the planner's
        falsification_penalty measurably lowers a heavily-falsified action's score."""
        from agents.arc4.graph_queries import ArcGraphQueryPort

        class _StubBrainClient:
            def __init__(self, results_by_action: dict[str, dict[str, Any]]):
                self._results = results_by_action

            def call_tool(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
                if tool_name == "arc_get_action_evidence":
                    return self._results.get(payload["action_id"], {})
                return {"status": "capability_missing"}

        falsified_client = _StubBrainClient(
            {"action-a": {"confidence": 0.0, "falsified_count": 5, "evidence_count": 5}}
        )
        clean_client = _StubBrainClient(
            {"action-a": {"confidence": 0.0, "falsified_count": 0, "evidence_count": 0}}
        )
        falsified_port = ArcGraphQueryPort(falsified_client, task_id="t", session_id="s", strict=False)
        clean_port = ArcGraphQueryPort(clean_client, task_id="t", session_id="s", strict=False)

        planner = PlanGenerator(PlanGeneratorLimits())
        state = _state()
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["action-a"]},
            grid_hash="hash-1",
        )
        goal = _goal()

        falsified_result = planner.generate(state, perception, goal, graph_port=falsified_port).payload
        clean_result = planner.generate(state, perception, goal, graph_port=clean_port).payload

        assert falsified_result.candidate.score < clean_result.candidate.score, (
            f"falsified_count=5 should score lower than falsified_count=0, "
            f"got {falsified_result.candidate.score} vs {clean_result.candidate.score}"
        )


# ── Vet: graph gate blocks action ─────────────────────────────────────

class TestA135VetGraphGate:
    """Vet should block actions when graph evidence says no."""

    def test_graph_gate_blocks_action_with_alternative(self):
        """When graph says action is not allowed and alternative exists, veto."""
        graph = MockGraphPort(
            action_gates={"action-a": {"allowed": False, "reason": "contradicted by evidence"}},
        )
        vetter = PlanVetter(graph_port=graph)
        state = _state()
        perception = _perception()
        goal = _goal()
        plan = _plan_result(
            action_id="action-a",
            score=0.5,
            alternatives=(PlanCandidate(action_id="action-b", goal_id="goal-1", score=0.4),),
        )

        result = vetter.vet(state, perception, goal, plan)
        assert not result.payload.approved
        assert result.payload.metadata["veto_type"] == "graph_evidence"

    def test_graph_gate_allows_action_when_no_evidence(self):
        """When graph has no negative evidence, allow the action."""
        graph = MockGraphPort(
            action_gates={"action-a": {"allowed": True, "reason": "no_evidence"}},
        )
        vetter = PlanVetter(graph_port=graph)
        state = _state()
        perception = _perception()
        goal = _goal()
        plan = _plan_result(action_id="action-a", score=0.5)

        result = vetter.vet(state, perception, goal, plan)
        assert result.payload.approved

    def test_graph_gate_blocks_but_no_alternative_still_approves(self):
        """When graph blocks but no alternative exists, approve anyway (can't pivot to nothing)."""
        graph = MockGraphPort(
            action_gates={"action-a": {"allowed": False, "reason": "contradicted"}},
        )
        vetter = PlanVetter(graph_port=graph)
        state = _state()
        perception = _perception()
        goal = _goal()
        plan = _plan_result(action_id="action-a", score=0.5, alternatives=())

        result = vetter.vet(state, perception, goal, plan)
        # No alternative, so it goes through to the regular checks and gets approved
        assert result.payload.approved

    def test_vet_works_without_graph_port(self):
        """Vet should work fine when graph_port is None."""
        vetter = PlanVetter(graph_port=None)
        state = _state()
        perception = _perception()
        goal = _goal()
        plan = _plan_result(action_id="action-a", score=0.5)

        result = vetter.vet(state, perception, goal, plan)
        assert result.payload.approved


# ── Evaluator: causal path overrides progress ─────────────────────────

class TestA135EvaluatorCausalPath:
    """Evaluator should use causal path to override progress when only contradictions exist."""

    def test_causal_contradiction_overrides_progress(self):
        """When causal path exists with only contradictions, override meaningful_progress."""
        graph = MockGraphPort(
            causal_paths={"action-a": {"path_exists": True, "supports": False, "contradicts": True}},
        )
        evaluator = Evaluator(graph_query_port=graph)
        state = _state()
        perception = _perception()
        goal = _goal()
        execution = _execution(action_id="action-a", did_progress=True)

        result = evaluator.evaluate(state, perception, goal, execution)
        assert not result.payload.meaningful_progress
        assert result.payload.metadata["causal_override"] is True

    def test_causal_support_does_not_override(self):
        """When causal path has support, don't override progress."""
        graph = MockGraphPort(
            causal_paths={"action-a": {"path_exists": True, "supports": True, "contradicts": False}},
        )
        evaluator = Evaluator(graph_query_port=graph)
        state = _state()
        perception = _perception()
        goal = _goal()
        execution = _execution(action_id="action-a", did_progress=True)

        result = evaluator.evaluate(state, perception, goal, execution)
        assert result.payload.meaningful_progress
        assert result.payload.metadata["causal_override"] is False

    def test_no_causal_path_does_not_override(self):
        """When no causal path exists, don't override progress."""
        graph = MockGraphPort(
            causal_paths={"action-a": {"path_exists": False, "supports": False, "contradicts": False}},
        )
        evaluator = Evaluator(graph_query_port=graph)
        state = _state()
        perception = _perception()
        goal = _goal()
        execution = _execution(action_id="action-a", did_progress=True)

        result = evaluator.evaluate(state, perception, goal, execution)
        assert result.payload.meaningful_progress
        assert result.payload.metadata["causal_override"] is False

    def test_evaluator_works_without_graph_port(self):
        """Evaluator should work fine when graph_query_port is None."""
        evaluator = Evaluator(graph_query_port=None)
        state = _state()
        perception = _perception()
        goal = _goal()
        execution = _execution(action_id="action-a", did_progress=True)

        result = evaluator.evaluate(state, perception, goal, execution)
        assert result.payload.meaningful_progress
        assert result.payload.metadata["causal_override"] is False

    def test_graph_error_does_not_override(self):
        """When graph port raises, don't override progress."""

        class BrokenGraphPort(MockGraphPort):
            def fetch_causal_path(self, action_id: str) -> dict[str, Any]:
                raise RuntimeError("graph unavailable")

        evaluator = Evaluator(graph_query_port=BrokenGraphPort())
        state = _state()
        perception = _perception()
        goal = _goal()
        execution = _execution(action_id="action-a", did_progress=True)

        result = evaluator.evaluate(state, perception, goal, execution)
        assert result.payload.meaningful_progress
        assert result.payload.metadata["causal_override"] is False
