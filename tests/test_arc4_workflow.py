from __future__ import annotations

from collections import deque
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.ports import WorkflowDependencies
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
    VetDecision,
    WorkflowDecision,
    WorkflowPhase,
    WorkflowState,
    WorkflowStatus,
)
from agents.arc4.workflow import WorkflowLimits, WorkflowOrchestrator


def _fake_annatar(state, perception, execution, evaluation, *, stall_reason=None, veto_reason=None, veto_alternative_action_id=None, readiness_report=None, resolve_report=None):
    """A250: `annatar` is a required WorkflowDependencies field now that it's
    unconditionally wired in production (since A202) -- this is the minimal
    non-terminating stand-in every test in this module (and every module
    that imports `_dependencies` from here) uses by default, so the general
    orchestrator-mechanics tests below don't need to care about Annatar's
    own decision logic at all. Tests that DO care override `deps.annatar`
    with their own mock after construction (see test_a202_annatar_
    orchestrator_integration.py's own pattern)."""
    return AnnatarOutcome(decision="advance")


def _scripted_phase(name: str, responses: list[PhaseResult], calls: list[str]):
    queue = deque(responses)

    def phase(*args):
        calls.append(name)
        if not queue:
            raise AssertionError(f"No scripted response left for {name}")
        return queue.popleft()

    return phase


def _perception(grid_hash: str) -> PhaseResult[PerceptionSnapshot]:
    return PhaseResult(
        phase=WorkflowPhase.PERCEIVE,
        payload=PerceptionSnapshot(observation={"grid": grid_hash}, grid_hash=grid_hash),
    )


def _goal(goal_id: str = "goal-1") -> PhaseResult[ResolvedGoal]:
    return PhaseResult(
        phase=WorkflowPhase.RESOLVE,
        payload=ResolvedGoal(selected=GoalHypothesis(goal_id=goal_id, description=goal_id, confidence=0.8)),
    )


def _plan(action_id: str = "action-1") -> PhaseResult[PlanningResult]:
    candidate = PlanCandidate(action_id=action_id, goal_id="goal-1", rationale=f"choose {action_id}")
    return PhaseResult(phase=WorkflowPhase.PLAN, payload=PlanningResult(candidate=candidate))


def _vet(approved: bool, action_id: str = "action-1", reason: str = "") -> PhaseResult[VetDecision]:
    candidate = PlanCandidate(action_id=action_id, goal_id="goal-1", rationale=f"choose {action_id}")
    payload = VetDecision(
        approved=approved,
        candidate=candidate if approved else None,
        reason=reason,
        alternative=None if approved else PlanCandidate(action_id="action-2", goal_id="goal-1", rationale="fallback"),
        should_replan=not approved,
    )
    return PhaseResult(
        phase=WorkflowPhase.VET,
        status=PhaseStatus.OK if approved else PhaseStatus.VETO,
        payload=payload,
        reason=reason or None,
    )


def _execute(action_id: str = "action-1", grid_hash: str = "grid-next") -> PhaseResult[ExecutionResult]:
    candidate = PlanCandidate(action_id=action_id, goal_id="goal-1", rationale=f"choose {action_id}")
    return PhaseResult(
        phase=WorkflowPhase.EXECUTE,
        payload=ExecutionResult(action_id=action_id, candidate=candidate, observation={"grid": grid_hash}, did_progress=True),
    )


def _evaluation(
    decision: WorkflowDecision,
    *,
    meaningful_progress: bool,
    reason: str = "",
    falsification_delta: int = 0,
) -> PhaseResult[EvaluationResult]:
    return PhaseResult(
        phase=WorkflowPhase.EVALUATE,
        status=PhaseStatus.TERMINATE if decision == WorkflowDecision.TERMINATE else PhaseStatus.OK,
        payload=EvaluationResult(
            decision=decision,
            meaningful_progress=meaningful_progress,
            reason=reason,
            falsification_delta=falsification_delta,
        ),
        reason=reason or None,
    )


def _dependencies(calls: list[str], overrides: dict[str, list[PhaseResult]] | None = None) -> WorkflowDependencies:
    overrides = overrides or {}
    return WorkflowDependencies(
        perceive=_scripted_phase("perceive", overrides.get("perceive", [_perception("grid-1")]), calls),
        resolve=_scripted_phase("resolve", overrides.get("resolve", [_goal()]), calls),
        plan=_scripted_phase("plan", overrides.get("plan", [_plan()]), calls),
        vet=_scripted_phase("vet", overrides.get("vet", [_vet(True)]), calls),
        execute=_scripted_phase("execute", overrides.get("execute", [_execute()]), calls),
        evaluate=_scripted_phase("evaluate", overrides.get("evaluate", [_evaluation(WorkflowDecision.TERMINATE, meaningful_progress=True, reason="done")]), calls),
        annatar=_fake_annatar,
    )


def test_phase_order_runs_in_fixed_sequence():
    calls: list[str] = []
    orchestrator = WorkflowOrchestrator(_dependencies(calls), limits=WorkflowLimits(max_cycles=3))

    result = orchestrator.run(WorkflowState(), {"grid": [[1]]})

    assert calls == ["perceive", "resolve", "plan", "vet", "execute", "evaluate"]
    assert result.status == WorkflowStatus.TERMINATED
    assert result.completed_cycles == 1


def test_budget_guard_blocks_work_before_first_cycle():
    calls: list[str] = []
    orchestrator = WorkflowOrchestrator(_dependencies(calls), limits=WorkflowLimits(max_cycles=0))

    result = orchestrator.run(WorkflowState(), {"grid": [[1]]})

    assert calls == []
    assert result.status == WorkflowStatus.BUDGET_EXHAUSTED
    assert result.reason == "budget_exhausted"


def test_crash_guard_captures_traceback():
    calls: list[str] = []

    def crashing_plan(*args):
        calls.append("plan")
        raise RuntimeError("boom")

    dependencies = WorkflowDependencies(
        perceive=_scripted_phase("perceive", [_perception("grid-1")], calls),
        resolve=_scripted_phase("resolve", [_goal()], calls),
        plan=crashing_plan,
        vet=_scripted_phase("vet", [_vet(True)], calls),
        execute=_scripted_phase("execute", [_execute()], calls),
        evaluate=_scripted_phase("evaluate", [_evaluation(WorkflowDecision.TERMINATE, meaningful_progress=True)], calls),
        annatar=_fake_annatar,
    )

    result = WorkflowOrchestrator(dependencies).run(WorkflowState(), {"grid": [[1]]})

    assert calls == ["perceive", "resolve", "plan"]
    assert result.status == WorkflowStatus.CRASHED
    assert "RuntimeError: boom" in result.traceback
    assert result.state.crash_traceback == result.traceback


def test_single_veto_triggers_one_replan_pass():
    calls: list[str] = []
    dependencies = _dependencies(
        calls,
        overrides={
            "vet": [_vet(False, reason="initial veto"), _vet(True, action_id="action-2")],
            "plan": [_plan("action-1"), _plan("action-2")],
            "resolve": [_goal(), _goal()],
        },
    )

    result = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=3)).run(WorkflowState(), {"grid": [[1]]})

    assert calls == ["perceive", "resolve", "plan", "vet", "resolve", "plan", "vet", "execute", "evaluate"]
    assert result.status == WorkflowStatus.TERMINATED
    assert result.state.replan_passes == 1
    assert result.state.latest_veto_reason == "initial veto"
    assert result.state.latest_veto_alternative is not None
    assert result.state.latest_veto_alternative.action_id == "action-2"



# A250: test_second_veto_skips_execution and
# test_stall_terminates_after_repeated_no_progress used to live here,
# pinning the no-Annatar-configured fallback's own outcomes (a bare
# `check_stall` ending the run directly as STALLED with no arbiter, and a
# second veto ending it directly as SKIPPED/second_veto). Both branches were
# deleted from workflow.py by A250 -- `annatar` has been unconditionally
# wired in production since A202, so they were permanently dead code.
# Confirmed via TDD: both tests failed (not errored) once the production
# branches were removed, proving they really did exercise exactly that code.
# The underlying mechanisms they covered are exercised with an
# Annatar-configured dependency set instead, in
# tests/test_a202_annatar_orchestrator_integration.py::
# TestSecondVetoRoutesThroughAnnatar (second-veto routing) and
# ::TestAnnatarControlFlow::test_stall_signal_folds_into_annatar_instead_of_independently_stalling
# (stall folding into the Annatar call instead of independently ending the
# run). See backlog/A250.md's Outcome for the full enumeration.
