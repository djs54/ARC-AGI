"""Tests for A251: goal_resolver.py::_query_llm and plan_generator.py::
_query_llm call `llm_port.chat(...)` with zero surrounding try/except -- a
raised exception there propagates uncaught through resolve()/generate(),
through workflow.py's outer except, ending the whole episode with
WorkflowStatus.CRASHED.

This is not a new discovery -- annatar_signals.py::resolve_llm_vote's own
docstring (A205) already named this exact gap in both files, then only
fixed a third, newer call site (resolve_llm_vote itself). This card closes
the two original call sites the same way: exactly one enclosing
`try/except Exception`, degrading to the same "no LLM patch" outcome each
caller already handles safely, and making the degradation visible via the
same `_degraded` flag family A237/A244 already established for
plan/vet/evaluate. See backlog/A251.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.goal_resolver import GoalResolver, GoalResolverLimits
from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.plan_vetter import PlanVetter
from agents.arc4.telemetry import ArcV2Telemetry
from agents.arc4.types import (
    AnnatarOutcome,
    ExecutionResult,
    GoalHypothesis,
    PerceivedEntity,
    PerceptionSnapshot,
    PhaseResult,
    PhaseStatus,
    PlanCandidate,
    ResolvedGoal,
    WorkflowPhase,
    WorkflowState,
)
from agents.arc4.ports import WorkflowDependencies
from agents.arc4.workflow import WorkflowLimits, WorkflowOrchestrator


# --- Shared fixtures ---------------------------------------------------------


class _RaisingLLMPort:
    """Every real LLMPort call raises -- simulates a transport failure that
    a different LLMPort implementation than SyncLLMPortAdapter might not
    absorb (e.g. no defensive wrapping at all)."""

    def __init__(self) -> None:
        self.call_count = 0

    def chat(self, messages):
        self.call_count += 1
        raise RuntimeError("llm transport unavailable")


class _HealthyLLMPort:
    def __init__(self, response: str) -> None:
        self.response = response
        self.call_count = 0

    def chat(self, messages):
        self.call_count += 1
        return self.response


def _perception_ambiguous() -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"grid": "grid-1"},
        grid_hash="grid-1",
        grid_shape=(2, 2),
        entities=(
            PerceivedEntity(kind="block", value="red", attributes={}),
            PerceivedEntity(kind="block", value="blue", attributes={}),
        ),
    )


def _perception_plan() -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"grid": "hash-1", "available_actions": ["ACTION6", "ACTION7"]},
        grid_hash="hash-1",
    )


def _goal(goal_id: str = "goal-1") -> ResolvedGoal:
    return ResolvedGoal(selected=GoalHypothesis(goal_id=goal_id, description="test goal", confidence=0.8))


# --- goal_resolver.py::_query_llm exception safety --------------------------


class TestGoalResolverLlmCallExceptionSafety:
    def test_raising_llm_port_does_not_propagate_and_resolve_completes(self):
        resolver = GoalResolver()
        llm = _RaisingLLMPort()

        result = resolver.resolve(WorkflowState(consecutive_no_progress_count=2), _perception_ambiguous(), llm_port=llm)

        assert llm.call_count == 1
        assert result.payload is not None
        # LLM patch failed -> escalation attempted but not applied, same as
        # the existing "unparseable response" outcome.
        assert result.payload.metadata["llm_escalated"] is False

    def test_raising_llm_port_sets_degraded_true(self):
        resolver = GoalResolver()
        llm = _RaisingLLMPort()

        result = resolver.resolve(WorkflowState(consecutive_no_progress_count=2), _perception_ambiguous(), llm_port=llm)

        assert result.payload.degraded is True

    def test_raising_llm_port_leaves_hypothesis_ranking_unchanged(self):
        """The pre-LLM hypothesis ranking should still be used, exactly as
        if _should_escalate_to_llm had decided not to escalate at all."""
        resolver = GoalResolver()
        llm = _RaisingLLMPort()

        resolved_without_llm = resolver.resolve(WorkflowState(), _perception_ambiguous())
        resolver_2 = GoalResolver()
        resolved_with_raising_llm = resolver_2.resolve(WorkflowState(consecutive_no_progress_count=2), _perception_ambiguous(), llm_port=llm)

        assert resolved_with_raising_llm.payload.selected.goal_id == resolved_without_llm.payload.selected.goal_id

    def test_healthy_llm_call_leaves_degraded_false(self):
        resolver = GoalResolver()
        llm = _HealthyLLMPort(
            response='{"goal_id": "block-red", "confidence": 0.91, "reason": "chosen"}'
        )

        result = resolver.resolve(WorkflowState(consecutive_no_progress_count=2), _perception_ambiguous(), llm_port=llm)

        assert llm.call_count == 1
        assert result.payload.degraded is False
        assert result.payload.metadata["llm_escalated"] is True

    def test_no_llm_port_leaves_degraded_false(self):
        resolver = GoalResolver()

        result = resolver.resolve(WorkflowState(), _perception_ambiguous())

        assert result.payload.degraded is False

    def test_degraded_does_not_leak_across_successive_resolve_calls(self):
        """GoalResolver is a single long-lived instance reused for every
        cycle of an episode -- a degraded cycle must not poison every
        subsequent cycle's ResolvedGoal.degraded."""
        resolver = GoalResolver()
        raising = _RaisingLLMPort()
        degraded_result = resolver.resolve(WorkflowState(consecutive_no_progress_count=2), _perception_ambiguous(), llm_port=raising)

        healthy = _HealthyLLMPort('{"goal_id": "block-red", "confidence": 0.9, "reason": "x"}')
        healthy_result = resolver.resolve(WorkflowState(consecutive_no_progress_count=2), _perception_ambiguous(), llm_port=healthy)

        assert degraded_result.payload.degraded is True
        assert healthy_result.payload.degraded is False


# --- plan_generator.py::_query_llm exception safety -------------------------


class TestPlanGeneratorLlmCallExceptionSafety:
    def test_raising_llm_port_does_not_propagate_and_generate_completes(self):
        planner = PlanGenerator(PlanGeneratorLimits())
        llm = _RaisingLLMPort()

        result = planner.generate(WorkflowState(), _perception_plan(), _goal(), llm_port=llm)

        assert llm.call_count == 1
        assert result.payload is not None
        assert result.payload.candidate is not None

    def test_raising_llm_port_sets_degraded_true_reaching_planning_result(self):
        planner = PlanGenerator(PlanGeneratorLimits())
        llm = _RaisingLLMPort()

        result = planner.generate(WorkflowState(), _perception_plan(), _goal(), llm_port=llm)

        assert result.payload.degraded is True

    def test_raising_llm_port_leaves_candidate_ranking_unchanged(self):
        planner_a = PlanGenerator(PlanGeneratorLimits())
        no_llm_result = planner_a.generate(WorkflowState(), _perception_plan(), _goal())

        planner_b = PlanGenerator(PlanGeneratorLimits())
        raising_llm_result = planner_b.generate(WorkflowState(), _perception_plan(), _goal(), llm_port=_RaisingLLMPort())

        assert raising_llm_result.payload.candidate.action_id == no_llm_result.payload.candidate.action_id

    def test_healthy_llm_call_leaves_degraded_false(self):
        planner = PlanGenerator(PlanGeneratorLimits())
        llm = _HealthyLLMPort('{"action_id": "ACTION7", "reason": "x"}')

        result = planner.generate(WorkflowState(), _perception_plan(), _goal(), llm_port=llm)

        assert llm.call_count == 1
        assert result.payload.degraded is False

    def test_no_llm_port_leaves_degraded_false(self):
        planner = PlanGenerator(PlanGeneratorLimits())

        result = planner.generate(WorkflowState(), _perception_plan(), _goal())

        assert result.payload.degraded is False

    def test_degraded_does_not_leak_across_successive_generate_calls(self):
        """PlanGenerator is a single long-lived instance reused for every
        cycle of an episode (arc_runtime/bundle.py) -- a degraded cycle
        must not poison every subsequent cycle's PlanningResult.degraded."""
        planner = PlanGenerator(PlanGeneratorLimits())
        degraded_result = planner.generate(WorkflowState(), _perception_plan(), _goal(), llm_port=_RaisingLLMPort())
        healthy_result = planner.generate(WorkflowState(), _perception_plan(), _goal(), llm_port=_HealthyLLMPort('{"action_id": "ACTION7", "reason": "x"}'))

        assert degraded_result.payload.degraded is True
        assert healthy_result.payload.degraded is False


# --- ResolvedGoal.to_dict/from_dict round-trip -------------------------------


class TestResolvedGoalDegradedFieldRoundTrip:
    def test_default_false_and_survives_round_trip(self):
        resolver = GoalResolver()
        result = resolver.resolve(WorkflowState(), _perception_ambiguous())

        assert result.payload.degraded is False
        restored = ResolvedGoal.from_dict(result.payload.to_dict())
        assert restored.degraded is False

    def test_true_value_survives_round_trip(self):
        resolver = GoalResolver()
        result = resolver.resolve(WorkflowState(consecutive_no_progress_count=2), _perception_ambiguous(), llm_port=_RaisingLLMPort())

        assert result.payload.degraded is True
        restored = ResolvedGoal.from_dict(result.payload.to_dict())
        assert restored.degraded is True


# --- WorkflowState.to_dict/from_dict round-trip ------------------------------


class TestWorkflowStateResolveDegradedFieldRoundTrip:
    def test_defaults_false_and_survives_round_trip(self):
        state = WorkflowState()
        assert state.resolve_degraded is False

        restored = WorkflowState.from_dict(state.to_dict())
        assert restored.resolve_degraded is False

    def test_true_value_survives_round_trip(self):
        state = WorkflowState(resolve_degraded=True)
        restored = WorkflowState.from_dict(state.to_dict())

        assert restored.resolve_degraded is True


# --- telemetry.py per-cycle summary -----------------------------------------


class TestTelemetrySurfacesResolveDegraded:
    def test_step_snapshot_surfaces_resolve_degraded(self):
        telemetry = ArcV2Telemetry(task_id="t1", game_id="g1")
        state = WorkflowState(resolve_degraded=True)

        snapshot = telemetry._step_snapshot((state,))

        assert snapshot["resolve_degraded"] is True

    def test_step_snapshot_defaults_false_when_state_missing_field(self):
        telemetry = ArcV2Telemetry(task_id="t1", game_id="g1")

        snapshot = telemetry._step_snapshot(())

        assert snapshot["resolve_degraded"] is False


# --- Integration: full workflow.py cycle with a raising LLM port -----------


def _perceive(state, observation):
    return PhaseResult(
        phase=WorkflowPhase.PERCEIVE, status=PhaseStatus.OK,
        payload=PerceptionSnapshot(observation=observation, grid_hash="h1", entities=(
            PerceivedEntity(kind="block", value="red", attributes={}),
            PerceivedEntity(kind="block", value="blue", attributes={}),
        )),
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


def _evaluate_result():
    from agents.arc4.types import EvaluationResult, WorkflowDecision
    return PhaseResult(
        phase=WorkflowPhase.EVALUATE, status=PhaseStatus.OK,
        payload=EvaluationResult(decision=WorkflowDecision.ADVANCE, meaningful_progress=True),
    )


def _fake_annatar(state, perception, execution, evaluation, **_kwargs):
    """A250: `annatar` is a required WorkflowDependencies field -- these
    tests are about resolve/plan degraded-visibility propagation, not
    Annatar's own decision logic, so a minimal non-terminating stand-in is
    enough."""
    return AnnatarOutcome(decision="advance")


def _make_dependencies(llm_port: Any) -> WorkflowDependencies:
    resolver = GoalResolver()
    plan_generator = PlanGenerator(PlanGeneratorLimits())
    vetter = PlanVetter()

    def resolve(state, perception):
        return resolver.resolve(state, perception, llm_port=llm_port)

    def plan(state, perception, goal):
        return plan_generator.generate(state, perception, goal, llm_port=llm_port)

    return WorkflowDependencies(
        perceive=_perceive,
        resolve=resolve,
        plan=plan,
        vet=vetter.vet,
        execute=_execute,
        evaluate=lambda *args, **kwargs: _evaluate_result(),
        annatar=_fake_annatar,
    )


class TestWorkflowIntegrationResolveDegradedPropagation:
    def test_full_cycle_with_raising_llm_port_sets_resolve_degraded_true(self):
        deps = _make_dependencies(_RaisingLLMPort())
        orchestrator = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=1))
        state = WorkflowState(consecutive_no_progress_count=2)

        orchestrator.run(state, {"available_actions": ["ACTION1"]})

        assert state.resolve_degraded is True

    def test_full_cycle_with_healthy_llm_port_leaves_resolve_degraded_false(self):
        deps = _make_dependencies(_HealthyLLMPort('{"goal_id": "block-red", "confidence": 0.9, "reason": "x"}'))
        orchestrator = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=1))
        state = WorkflowState(consecutive_no_progress_count=2)

        orchestrator.run(state, {"available_actions": ["ACTION1"]})

        assert state.resolve_degraded is False

    def test_full_cycle_with_no_llm_configured_leaves_resolve_degraded_false(self):
        deps = _make_dependencies(None)
        orchestrator = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=1))
        state = WorkflowState()

        orchestrator.run(state, {"available_actions": ["ACTION1"]})

        assert state.resolve_degraded is False
