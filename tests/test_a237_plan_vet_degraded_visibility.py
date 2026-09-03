"""Tests for A237: plan_generator.py/plan_vetter.py absorb graph_port
exceptions with zero visibility, unlike the established annatar_degraded
(A205) / readiness_gate_partial (A224) precedent this card extends to the
plan/vet phases.

plan_generator.py::_build_candidates has three `except Exception: pass`/
`continue` sites around graph_port calls (fetch_per_action_evidence,
fetch_rules_for_action in _build_candidates; fetch_untested_actions in
_available_actions); plan_vetter.py::_check_graph_gate fails open on any
exception with the failure reason discarded, and _has_live_rule_evidence
degrades to False (no override) the same way. Both fallback *behaviors* are
unchanged by this card -- only a new `degraded: bool` field on
PlanningResult/VetDecision (and WorkflowState.plan_degraded/vet_degraded,
telemetry.py's per-cycle summary) makes the fact that a graph_port call
raised queryable instead of silently absorbed. See backlog/A237.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.plan_vetter import PlanVetter
from agents.arc4.telemetry import ArcV2Telemetry
from agents.arc4.types import (
    AnnatarOutcome,
    EvaluationResult,
    ExecutionResult,
    GoalHypothesis,
    PerceptionSnapshot,
    PhaseResult,
    PhaseStatus,
    PlanCandidate,
    PlanningResult,
    ResolvedGoal,
    WorkflowDecision,
    WorkflowPhase,
    WorkflowState,
)
from agents.arc4.ports import WorkflowDependencies
from agents.arc4.workflow import WorkflowLimits, WorkflowOrchestrator


# --- Shared fixtures --------------------------------------------------------


def _state(**overrides) -> WorkflowState:
    defaults = dict(
        step_index=0,
        action_attempt_counts={},
        action_falsification_counts={},
        consecutive_no_progress_count=0,
    )
    defaults.update(overrides)
    return WorkflowState(**defaults)


def _perception(actions: list[str] | None = None) -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"grid": "hash-1", "available_actions": actions or ["ACTION1"]},
        grid_hash="hash-1",
    )


def _goal(goal_id: str = "goal-1") -> ResolvedGoal:
    return ResolvedGoal(
        selected=GoalHypothesis(goal_id=goal_id, description="test goal", confidence=0.8),
    )


def _plan_for_vetter() -> PlanningResult:
    return PlanningResult(
        candidate=PlanCandidate(action_id="ACTION1", goal_id="goal-1", score=0.5, book_id="ACTION1"),
        alternatives=(PlanCandidate(action_id="ACTION2", goal_id="goal-1", score=0.4, book_id="ACTION2"),),
    )


class _HealthyGraphPort:
    """Every graph_port call plan_generator.py/plan_vetter.py touch, all
    succeeding normally -- the regression baseline these tests contrast
    against."""

    def fetch_goal_evidence(self, perception: Any, goal: Any = None) -> Any:
        return {}

    def fetch_untested_actions(self) -> list[str]:
        return []

    def fetch_per_action_evidence(self, action_id: str) -> dict[str, Any]:
        return {"supports": 1, "contradictions": 0, "confidence": 0.5, "attempts": 1}

    def fetch_rules_for_action(self, action_id: str) -> list[dict[str, Any]]:
        return [{"rule_id": "r1", "confidence": 0.6, "falsified": False}]

    def check_action_gate(self, action_id: str) -> dict[str, Any]:
        return {"allowed": True, "reason": "no_evidence"}


# --- plan_generator.py::_build_candidates / _available_actions -------------


class TestPlanGeneratorDegradedVisibility:
    def test_fetch_per_action_evidence_raising_sets_degraded_true(self):
        class _Port(_HealthyGraphPort):
            def fetch_per_action_evidence(self, action_id: str) -> dict[str, Any]:
                raise RuntimeError("hippocampy MCP not available")

        planner = PlanGenerator(PlanGeneratorLimits())
        result = planner.generate(_state(), _perception(), _goal(), graph_port=_Port())

        assert result.payload.degraded is True
        # Unchanged fallback behavior: a candidate is still produced.
        assert result.payload.candidate is not None

    def test_fetch_rules_for_action_raising_sets_degraded_true(self):
        """fetch_per_action_evidence succeeds but fetch_rules_for_action
        raises -- confirms this is a second, independently-wired site, not
        just the first one implemented."""

        class _Port(_HealthyGraphPort):
            def fetch_rules_for_action(self, action_id: str) -> list[dict[str, Any]]:
                raise RuntimeError("hippocampy MCP not available")

        planner = PlanGenerator(PlanGeneratorLimits())
        result = planner.generate(_state(), _perception(), _goal(), graph_port=_Port())

        assert result.payload.degraded is True
        assert result.payload.candidate is not None

    def test_fetch_untested_actions_raising_sets_degraded_true(self):
        """fetch_untested_actions lives in _available_actions, a different
        method than the other two (which live in _build_candidates) --
        confirms the flag still threads through to the same
        PlanningResult.degraded."""

        class _Port(_HealthyGraphPort):
            def fetch_untested_actions(self) -> list[str]:
                raise RuntimeError("hippocampy MCP not available")

        planner = PlanGenerator(PlanGeneratorLimits())
        result = planner.generate(_state(), _perception(), _goal(), graph_port=_Port())

        assert result.payload.degraded is True
        assert result.payload.candidate is not None

    def test_no_graph_port_leaves_degraded_false(self):
        """The existing, correct 'no graph configured' case must NOT be
        conflated with a real exception -- the single most important
        distinction this card draws."""
        planner = PlanGenerator(PlanGeneratorLimits())
        result = planner.generate(_state(), _perception(), _goal(), graph_port=None)

        assert result.payload.degraded is False

    def test_healthy_graph_port_leaves_degraded_false(self):
        planner = PlanGenerator(PlanGeneratorLimits())
        result = planner.generate(_state(), _perception(), _goal(), graph_port=_HealthyGraphPort())

        assert result.payload.degraded is False

    def test_degraded_does_not_leak_across_successive_generate_calls(self):
        """PlanGenerator is a single long-lived instance reused for every
        cycle of an episode (arc_runtime/bundle.py) -- a degraded cycle must
        not poison every subsequent cycle's PlanningResult.degraded."""

        class _RaisingPort(_HealthyGraphPort):
            def fetch_untested_actions(self) -> list[str]:
                raise RuntimeError("hippocampy MCP not available")

        planner = PlanGenerator(PlanGeneratorLimits())
        degraded_result = planner.generate(_state(), _perception(), _goal(), graph_port=_RaisingPort())
        healthy_result = planner.generate(_state(), _perception(), _goal(), graph_port=_HealthyGraphPort())

        assert degraded_result.payload.degraded is True
        assert healthy_result.payload.degraded is False


# --- plan_vetter.py::_check_graph_gate / _has_live_rule_evidence -----------


class TestPlanVetterDegradedVisibility:
    def test_check_graph_gate_raising_sets_degraded_true_approved_unchanged(self):
        """Fail-open behavior (allowed=True on any exception) is unchanged
        -- with a fresh state (no falsification/repetition history), this
        still results in approved=True exactly as today."""

        class _Port(_HealthyGraphPort):
            def check_action_gate(self, action_id: str) -> dict[str, Any]:
                raise RuntimeError("hippocampy MCP not available")

        vetter = PlanVetter(graph_port=_Port())
        result = vetter.vet(_state(), _perception(), _goal(), _plan_for_vetter())

        assert result.payload.degraded is True
        assert result.payload.approved is True

    def test_has_live_rule_evidence_raising_sets_degraded_true_override_unchanged(self):
        """check_action_gate denies; fetch_rules_for_action (consulted for
        the A232 override) raises. Override behavior is unchanged -- it
        still degrades to False (no override), so the veto stands."""

        class _Port:
            def check_action_gate(self, action_id: str) -> dict[str, Any]:
                return {"allowed": False, "reason": "falsified 3 times"}

            def fetch_rules_for_action(self, action_id: str) -> list[dict[str, Any]]:
                raise RuntimeError("hippocampy MCP not available")

        vetter = PlanVetter(graph_port=_Port())
        result = vetter.vet(_state(), _perception(), _goal(), _plan_for_vetter())

        assert result.payload.degraded is True
        assert result.payload.approved is False
        assert result.payload.metadata["veto_type"] == "graph_evidence"

    def test_healthy_graph_port_leaves_degraded_false(self):
        vetter = PlanVetter(graph_port=_HealthyGraphPort())
        result = vetter.vet(_state(), _perception(), _goal(), _plan_for_vetter())

        assert result.payload.degraded is False

    def test_no_graph_port_leaves_degraded_false(self):
        vetter = PlanVetter(graph_port=None)
        result = vetter.vet(_state(), _perception(), _goal(), _plan_for_vetter())

        assert result.payload.degraded is False

    def test_degraded_does_not_leak_across_successive_vet_calls(self):
        """PlanVetter is likewise a single long-lived instance reused across
        an episode's cycles -- a degraded cycle must not poison every
        subsequent vet() call's VetDecision.degraded."""

        class _RaisingPort(_HealthyGraphPort):
            def check_action_gate(self, action_id: str) -> dict[str, Any]:
                raise RuntimeError("hippocampy MCP not available")

        vetter = PlanVetter(graph_port=_RaisingPort())
        degraded_result = vetter.vet(_state(), _perception(), _goal(), _plan_for_vetter())

        vetter._graph_port = _HealthyGraphPort()
        healthy_result = vetter.vet(_state(), _perception(), _goal(), _plan_for_vetter())

        assert degraded_result.payload.degraded is True
        assert healthy_result.payload.degraded is False


# --- WorkflowState.to_dict/from_dict round-trip -----------------------------


class TestWorkflowStateDegradedFieldsRoundTrip:
    def test_defaults_false_and_survive_round_trip(self):
        state = WorkflowState()
        assert state.plan_degraded is False
        assert state.vet_degraded is False

        restored = WorkflowState.from_dict(state.to_dict())
        assert restored.plan_degraded is False
        assert restored.vet_degraded is False

    def test_true_values_survive_round_trip(self):
        state = WorkflowState(plan_degraded=True, vet_degraded=True)
        restored = WorkflowState.from_dict(state.to_dict())

        assert restored.plan_degraded is True
        assert restored.vet_degraded is True


# --- telemetry.py per-cycle summary -----------------------------------------


class TestTelemetrySurfacesDegradedFlags:
    def test_step_snapshot_surfaces_plan_and_vet_degraded(self):
        telemetry = ArcV2Telemetry(task_id="t1", game_id="g1")
        state = _state(plan_degraded=True, vet_degraded=False)

        snapshot = telemetry._step_snapshot((state,))

        assert snapshot["plan_degraded"] is True
        assert snapshot["vet_degraded"] is False

    def test_step_snapshot_defaults_false_when_state_missing_fields(self):
        """getattr(..., False) degrade pattern -- must not raise/KeyError
        for a state object that predates this card (or is None)."""
        telemetry = ArcV2Telemetry(task_id="t1", game_id="g1")

        snapshot = telemetry._step_snapshot(())

        assert snapshot["plan_degraded"] is False
        assert snapshot["vet_degraded"] is False


# --- Integration: full workflow.py cycle with a raising graph_port ---------
#
# Per the A237 plan's live-verify section: a targeted integration test
# exercising the full workflow.py cycle with a raising graph_port is an
# acceptable substitute for a real live degraded-daemon run when arranging
# one isn't safe/practical (this session did not stop the shared hippocampy
# daemon other work might depend on -- see the card's Outcome for what was
# actually live-verified instead).


class _OfflineGraphPort:
    """Simulates the exact live scenario A237 documents: the hippocampy
    brain daemon becomes unreachable mid-episode. Every graph_port call
    plan_generator.py/plan_vetter.py actually guard with try/except raises;
    fetch_goal_evidence stays healthy since _fetch_graph_records calls it
    with no try/except at all (out of this card's scope -- not one of the
    three/two sites A237 covers)."""

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
            did_progress=False,
        ),
    )


def _evaluate(state, perception, goal, execution):
    return PhaseResult(
        phase=WorkflowPhase.EVALUATE, status=PhaseStatus.OK,
        payload=EvaluationResult(decision=WorkflowDecision.CONTINUE, meaningful_progress=False),
    )


def _fake_annatar(state, perception, execution, evaluation, **_kwargs):
    """A250: `annatar` is a required WorkflowDependencies field now that
    it's unconditionally wired in production (since A202) -- this module's
    tests are about plan/vet degraded-visibility propagation, not Annatar's
    own decision logic, so a minimal non-terminating stand-in is enough."""
    return AnnatarOutcome(decision="advance")


def _make_dependencies(graph_port: Any) -> WorkflowDependencies:
    plan_generator = PlanGenerator(PlanGeneratorLimits())
    vetter = PlanVetter(graph_port=graph_port)

    def plan(state, perception, goal):
        return plan_generator.generate(state, perception, goal, graph_port=graph_port)

    return WorkflowDependencies(
        perceive=_perceive,
        resolve=_resolve,
        plan=plan,
        vet=vetter.vet,
        execute=_execute,
        evaluate=_evaluate,
        annatar=_fake_annatar,
    )


class TestWorkflowIntegrationDegradedPropagation:
    def test_full_cycle_with_raising_graph_port_sets_state_flags_true(self):
        deps = _make_dependencies(_OfflineGraphPort())
        orchestrator = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=1))
        state = WorkflowState()

        orchestrator.run(state, {"available_actions": ["ACTION1"]})

        assert state.plan_degraded is True
        assert state.vet_degraded is True

    def test_full_cycle_with_healthy_graph_port_leaves_state_flags_false(self):
        deps = _make_dependencies(_HealthyGraphPort())
        orchestrator = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=1))
        state = WorkflowState()

        orchestrator.run(state, {"available_actions": ["ACTION1"]})

        assert state.plan_degraded is False
        assert state.vet_degraded is False
