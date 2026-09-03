"""Tests for A244: evaluator.py absorbs graph_port exceptions with zero
visibility, unlike the established annatar_degraded (A205) /
readiness_gate_partial (A224) / plan_degraded / vet_degraded (A237)
precedent this card extends to the evaluate phase -- the one file A237's
own card explicitly named as deferred-but-not-forgotten scope.

evaluate()'s `fetch_causal_path` call and `_action_space_exhausted`'s
`fetch_untested_actions` call each have an `except Exception: pass`/
`except Exception: pass -> fall through` site with zero visibility on
exception. Both fallback *behaviors* are unchanged by this card -- only a
new `degraded: bool` field on EvaluationResult (and
WorkflowState.evaluate_degraded, telemetry.py's per-cycle summary) makes
the fact that a graph_port call raised queryable instead of silently
absorbed. See backlog/A244.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.evaluator import EvaluationLimits, Evaluator
from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.plan_vetter import PlanVetter
from agents.arc4.telemetry import ArcV2Telemetry
from agents.arc4.types import (
    ExecutionResult,
    GoalHypothesis,
    PerceptionSnapshot,
    PhaseResult,
    PhaseStatus,
    PlanCandidate,
    ResolvedGoal,
    WorkflowDecision,
    WorkflowPhase,
    WorkflowState,
)
from agents.arc4.ports import WorkflowDependencies
from agents.arc4.workflow import WorkflowLimits, WorkflowOrchestrator


# --- Shared fixtures --------------------------------------------------------


def _goal(goal_id: str = "goal-1") -> ResolvedGoal:
    return ResolvedGoal(selected=GoalHypothesis(goal_id=goal_id, description=goal_id, confidence=0.8))


def _perception() -> PerceptionSnapshot:
    return PerceptionSnapshot(observation={"grid": [[1]]}, grid_hash="grid-1")


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
    """Every graph_port call evaluate()/_action_space_exhausted touch, all
    succeeding normally -- the regression baseline these tests contrast
    against."""

    def fetch_causal_path(self, action_id: str) -> dict[str, Any]:
        return {"path_exists": False}

    def fetch_untested_actions(self) -> list[str]:
        return ["ACTION2"]


# --- evaluate()'s fetch_causal_path except site -----------------------------


class TestFetchCausalPathDegradedVisibility:
    def test_raising_fetch_causal_path_sets_degraded_true_override_unchanged(self):
        """meaningful_progress must stay True (the exception path already
        skips the causal override) -- only visibility is added."""

        class _Port(_HealthyGraphPort):
            def fetch_causal_path(self, action_id: str) -> dict[str, Any]:
                raise RuntimeError("hippocampy MCP not available")

        evaluator = Evaluator(graph_query_port=_Port())
        result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), _execution(did_progress=True))

        assert result.payload is not None
        assert result.payload.degraded is True
        assert result.payload.meaningful_progress is True
        assert result.payload.metadata["causal_override"] is False
        assert result.payload.metadata["causal_path"] == {}

    def test_no_graph_port_leaves_degraded_false(self):
        """The existing, correct 'no graph configured' case must NOT be
        conflated with a real exception -- the single most important
        distinction this card draws."""
        evaluator = Evaluator(graph_query_port=None)
        result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), _execution(did_progress=True))

        assert result.payload is not None
        assert result.payload.degraded is False

    def test_healthy_graph_port_leaves_degraded_false(self):
        evaluator = Evaluator(graph_query_port=_HealthyGraphPort())
        result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), _execution(did_progress=True))

        assert result.payload is not None
        assert result.payload.degraded is False

    def test_degraded_does_not_leak_across_successive_evaluate_calls(self):
        """Evaluator is a single long-lived instance reused for every cycle
        of an episode (arc_runtime/bundle.py) -- a degraded cycle must not
        poison every subsequent cycle's EvaluationResult.degraded."""

        class _RaisingPort(_HealthyGraphPort):
            def fetch_causal_path(self, action_id: str) -> dict[str, Any]:
                raise RuntimeError("hippocampy MCP not available")

        evaluator = Evaluator(graph_query_port=_RaisingPort())
        degraded_result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), _execution(did_progress=True))

        evaluator._graph_query_port = _HealthyGraphPort()
        healthy_result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), _execution(did_progress=True))

        assert degraded_result.payload.degraded is True
        assert healthy_result.payload.degraded is False


# --- _action_space_exhausted's fetch_untested_actions except site ----------


class TestActionSpaceExhaustedDegradedVisibility:
    def test_raising_fetch_untested_actions_sets_degraded_true_source_unchanged(self):
        """exhaustion_source must stay 'threshold_only' (the exception path
        already falls through to it) -- only visibility is added. Without
        this card, that value is indistinguishable from 'no graph configured
        at all'."""

        class _Port(_HealthyGraphPort):
            def fetch_untested_actions(self) -> list[str]:
                raise RuntimeError("hippocampy MCP not available")

        state = WorkflowState(action_attempt_counts={"move": 4})
        evaluator = Evaluator(graph_query_port=_Port(), limits=EvaluationLimits(exhausted_action_attempt_threshold=4))
        result = evaluator.evaluate(state, _perception(), _goal(), _execution(action_id="move-right", did_progress=False))

        assert result.payload is not None
        assert result.payload.degraded is True
        assert result.payload.metadata["action_space_exhausted"] is True
        assert result.payload.metadata["exhaustion_source"] == "threshold_only"

    def test_no_fetch_untested_actions_attribute_leaves_degraded_false(self):
        """A graph_port that simply doesn't implement fetch_untested_actions
        (getattr(..., None) returns None, the existing optional-method
        convention) is not an exception -- must not be conflated with one."""

        class _Port:
            def fetch_causal_path(self, action_id: str) -> dict[str, Any]:
                return {"path_exists": False}

        state = WorkflowState(action_attempt_counts={"move": 4})
        evaluator = Evaluator(graph_query_port=_Port(), limits=EvaluationLimits(exhausted_action_attempt_threshold=4))
        result = evaluator.evaluate(state, _perception(), _goal(), _execution(action_id="move-right", did_progress=False))

        assert result.payload is not None
        assert result.payload.degraded is False
        assert result.payload.metadata["exhaustion_source"] == "threshold_only"

    def test_no_graph_port_leaves_degraded_false(self):
        state = WorkflowState(action_attempt_counts={"move": 4})
        evaluator = Evaluator(graph_query_port=None, limits=EvaluationLimits(exhausted_action_attempt_threshold=4))
        result = evaluator.evaluate(state, _perception(), _goal(), _execution(action_id="move-right", did_progress=False))

        assert result.payload is not None
        assert result.payload.degraded is False
        assert result.payload.metadata["exhaustion_source"] == "threshold_only"

    def test_healthy_graph_port_leaves_degraded_false_and_not_exhausted(self):
        state = WorkflowState(action_attempt_counts={"move": 4})
        evaluator = Evaluator(graph_query_port=_HealthyGraphPort(), limits=EvaluationLimits(exhausted_action_attempt_threshold=4))
        result = evaluator.evaluate(state, _perception(), _goal(), _execution(action_id="move-right", did_progress=False))

        assert result.payload is not None
        assert result.payload.degraded is False
        assert result.payload.metadata["action_space_exhausted"] is False


# --- EvaluationResult.to_dict/from_dict round-trip --------------------------


class TestEvaluationResultDegradedFieldRoundTrip:
    def test_default_false_and_survives_round_trip(self):
        evaluator = Evaluator(graph_query_port=None)
        result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), _execution(did_progress=True))

        assert result.payload.degraded is False
        restored = type(result.payload).from_dict(result.payload.to_dict())
        assert restored.degraded is False

    def test_true_value_survives_round_trip(self):
        class _Port(_HealthyGraphPort):
            def fetch_causal_path(self, action_id: str) -> dict[str, Any]:
                raise RuntimeError("hippocampy MCP not available")

        evaluator = Evaluator(graph_query_port=_Port())
        result = evaluator.evaluate(WorkflowState(), _perception(), _goal(), _execution(did_progress=True))

        assert result.payload.degraded is True
        restored = type(result.payload).from_dict(result.payload.to_dict())
        assert restored.degraded is True


# --- WorkflowState.to_dict/from_dict round-trip -----------------------------


class TestWorkflowStateEvaluateDegradedFieldRoundTrip:
    def test_defaults_false_and_survives_round_trip(self):
        state = WorkflowState()
        assert state.evaluate_degraded is False

        restored = WorkflowState.from_dict(state.to_dict())
        assert restored.evaluate_degraded is False

    def test_true_value_survives_round_trip(self):
        state = WorkflowState(evaluate_degraded=True)
        restored = WorkflowState.from_dict(state.to_dict())

        assert restored.evaluate_degraded is True


# --- telemetry.py per-cycle summary -----------------------------------------


class TestTelemetrySurfacesEvaluateDegraded:
    def test_step_snapshot_surfaces_evaluate_degraded(self):
        telemetry = ArcV2Telemetry(task_id="t1", game_id="g1")
        state = WorkflowState(evaluate_degraded=True)

        snapshot = telemetry._step_snapshot((state,))

        assert snapshot["evaluate_degraded"] is True

    def test_step_snapshot_defaults_false_when_state_missing_field(self):
        """getattr(..., False) degrade pattern -- must not raise/KeyError
        for a state object that predates this card (or is None)."""
        telemetry = ArcV2Telemetry(task_id="t1", game_id="g1")

        snapshot = telemetry._step_snapshot(())

        assert snapshot["evaluate_degraded"] is False


# --- Integration: full workflow.py cycle with a raising graph_port ---------
#
# Per the A237 plan's live-verify section (mirrored here per A244's own
# plan): a targeted integration test exercising the full workflow.py cycle
# with a raising graph_port is an acceptable substitute for a real live
# degraded-daemon run when arranging one isn't safe/practical (this session
# did not stop the shared hippocampy daemon other work might depend on --
# see the card's Outcome for what was actually live-verified instead).


class _OfflineGraphPort:
    """Simulates the exact live scenario A237/A244 document: the hippocampy
    brain daemon becomes unreachable mid-episode. fetch_causal_path and
    fetch_untested_actions (the two evaluate-phase sites A244 covers) both
    raise; the plan/vet-phase sites this port also touches raise too, so
    plan_degraded/vet_degraded/evaluate_degraded are all exercised together
    in one real orchestrator cycle."""

    def fetch_goal_evidence(self, perception: Any, goal: Any = None) -> Any:
        return {}

    def fetch_untested_actions(self) -> list[str]:
        raise ConnectionError("hippocampy MCP not available")

    def fetch_per_action_evidence(self, action_id: str) -> dict[str, Any]:
        raise ConnectionError("hippocampy MCP not available")

    def fetch_rules_for_action(self, action_id: str) -> list[dict[str, Any]]:
        raise ConnectionError("hippocampy MCP not available")

    def check_action_gate(self, action_id: str) -> dict[str, Any]:
        raise ConnectionError("hippocampy MCP not available")

    def fetch_causal_path(self, action_id: str) -> dict[str, Any]:
        raise ConnectionError("hippocampy MCP not available")


def _perceive(state, observation):
    return PhaseResult(
        phase=WorkflowPhase.PERCEIVE, status=PhaseStatus.OK,
        payload=PerceptionSnapshot(observation=observation, grid_hash="h1", entities=()),
    )


def _resolve(state, perception):
    return PhaseResult(
        phase=WorkflowPhase.RESOLVE, status=PhaseStatus.OK,
        payload=ResolvedGoal(selected=GoalHypothesis(goal_id="g1", description="d", confidence=0.5)),
    )


def _execute(state, perception, goal, vet_decision):
    return PhaseResult(
        phase=WorkflowPhase.EXECUTE, status=PhaseStatus.OK,
        payload=ExecutionResult(
            action_id=vet_decision.candidate.action_id,
            candidate=vet_decision.candidate,
            observation={},
            did_progress=True,
        ),
    )


def _make_dependencies(graph_port: Any) -> WorkflowDependencies:
    plan_generator = PlanGenerator(PlanGeneratorLimits())
    vetter = PlanVetter(graph_port=graph_port)
    evaluator = Evaluator(graph_query_port=graph_port)

    def plan(state, perception, goal):
        return plan_generator.generate(state, perception, goal, graph_port=graph_port)

    return WorkflowDependencies(
        perceive=_perceive,
        resolve=_resolve,
        plan=plan,
        vet=vetter.vet,
        execute=_execute,
        evaluate=evaluator.evaluate,
    )


class TestWorkflowIntegrationEvaluateDegradedPropagation:
    def test_full_cycle_with_raising_graph_port_sets_evaluate_degraded_true(self):
        deps = _make_dependencies(_OfflineGraphPort())
        orchestrator = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=1))
        state = WorkflowState()

        orchestrator.run(state, {"available_actions": ["ACTION1"]})

        assert state.evaluate_degraded is True

    def test_full_cycle_with_healthy_graph_port_leaves_evaluate_degraded_false(self):
        class _Healthy(_HealthyGraphPort):
            def fetch_goal_evidence(self, perception: Any, goal: Any = None) -> Any:
                return {}

            def fetch_per_action_evidence(self, action_id: str) -> dict[str, Any]:
                return {"supports": 1, "contradictions": 0, "confidence": 0.5, "attempts": 1}

            def fetch_rules_for_action(self, action_id: str) -> list[dict[str, Any]]:
                return [{"rule_id": "r1", "confidence": 0.6, "falsified": False}]

            def check_action_gate(self, action_id: str) -> dict[str, Any]:
                return {"allowed": True, "reason": "no_evidence"}

        deps = _make_dependencies(_Healthy())
        orchestrator = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=1))
        state = WorkflowState()

        orchestrator.run(state, {"available_actions": ["ACTION1"]})

        assert state.evaluate_degraded is False
