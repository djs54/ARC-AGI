"""Tests for A242: `state.consecutive_no_progress_count` no longer accrues
during readiness-probe cycles (including an A241-granted resumed probe
window), mirroring A230's own established precedent of scoping
`annatar_unproductive_anchor_streak` to non-probe (`readiness_report is
None`) cycles.

Mechanism under test: `cycle_policy.py::record_evaluation_outcome` gained a
`count_toward_no_progress: bool = True` parameter; `workflow.py`'s
probe-path call site (inside the `if probe_candidate is not None:` block)
passes `count_toward_no_progress=False`, while the goal-directed call site
is untouched (keeps the default `True`). Falsification-count bookkeeping
happens unconditionally in both cases -- only the no-progress count itself
is scoped.

Test groups, matching the plan's TDD list (backlog/plans/A-242-no-progress-
count-probe-phase-exclusion.md):
  - TestRecordEvaluationOutcome: the pure cycle_policy.py function itself.
  - TestRecordEvaluationState: workflow.py's `_record_evaluation_state`
    staticmethod, at both its probe-path and goal-directed call shapes.
  - TestWorkflowResumeMappingNoProgressExclusion: the A241 interaction --
    a real WorkflowOrchestrator.run(), proving (a) a resumed probe window's
    cycles are automatically excluded because they re-enter the same
    probe-path code, and (b) the goal-directed count accumulated BEFORE a
    resume is preserved (not reset) once goal-directed play resumes after
    the remap -- consecutive_no_progress_count answers a different
    question than annatar_unproductive_anchor_streak (which A241 resets to
    0 on resume) and is not reset here.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.annatar_state_machine import ReadinessStatus
from agents.arc4.cycle_policy import record_evaluation_outcome
from agents.arc4.types import (
    AnnatarOutcome,
    EvaluationResult,
    ExecutionResult,
    GoalHypothesis,
    PerceivedEntity,
    PerceptionSnapshot,
    PhaseResult,
    PhaseStatus,
    PlanCandidate,
    ResolvedGoal,
    VetDecision,
    WorkflowDecision,
    WorkflowPhase,
    WorkflowState,
    WorkflowStatus,
)
from agents.arc4.ports import WorkflowDependencies
from agents.arc4.workflow import WorkflowLimits, WorkflowOrchestrator


# --- Group 1: cycle_policy.py::record_evaluation_outcome, the pure function ---


class TestRecordEvaluationOutcome:
    def test_count_toward_no_progress_false_leaves_count_unchanged_on_no_progress(self):
        falsification_counts: dict[str, int] = {}

        result = record_evaluation_outcome(
            no_progress_count=5,
            falsification_counts=falsification_counts,
            action_key="ACTION6@1,1",
            meaningful_progress=False,
            falsification_delta=1,
            count_toward_no_progress=False,
        )

        assert result == 5, "count must stay exactly as passed in, not increment"

    def test_count_toward_no_progress_false_still_updates_falsification_counts(self):
        falsification_counts: dict[str, int] = {}

        record_evaluation_outcome(
            no_progress_count=0,
            falsification_counts=falsification_counts,
            action_key="ACTION6@1,1",
            meaningful_progress=False,
            falsification_delta=2,
            count_toward_no_progress=False,
        )

        assert falsification_counts == {"ACTION6@1,1": 2}, "falsification bookkeeping is unconditional -- only the no-progress count is scoped"

    def test_count_toward_no_progress_true_default_increments_exactly_as_before(self):
        falsification_counts: dict[str, int] = {}

        result = record_evaluation_outcome(
            no_progress_count=5,
            falsification_counts=falsification_counts,
            action_key="ACTION6@1,1",
            meaningful_progress=False,
            falsification_delta=1,
        )

        assert result == 6, "default behavior (goal-directed call site) is the exact pre-A242 increment"

    def test_meaningful_progress_resets_to_zero_regardless_of_count_toward_no_progress(self):
        falsification_counts: dict[str, int] = {}

        result = record_evaluation_outcome(
            no_progress_count=7,
            falsification_counts=falsification_counts,
            action_key="ACTION6@1,1",
            meaningful_progress=True,
            falsification_delta=0,
            count_toward_no_progress=False,
        )

        assert result == 0, "real progress is real progress wherever it happens -- the flag only affects the no-progress branch"
        assert falsification_counts == {}, "meaningful_progress short-circuits before falsification bookkeeping, unaffected by this card"


# --- Group 2: workflow.py::_record_evaluation_state at both call shapes ---


def _execution(action_id: str = "ACTION6@1,1") -> ExecutionResult:
    candidate = PlanCandidate(action_id=action_id, goal_id="g1", metadata={})
    return ExecutionResult(action_id=action_id, candidate=candidate, observation={"grid": "h"})


def _evaluation(*, meaningful_progress: bool) -> EvaluationResult:
    return EvaluationResult(
        decision=WorkflowDecision.CONTINUE,
        meaningful_progress=meaningful_progress,
        metadata={"grid_changed": False},
        falsification_delta=1,
    )


class TestRecordEvaluationState:
    def test_probe_path_calls_do_not_increment_no_progress_count(self):
        """Mirrors the probe-path call site's actual invocation shape
        (workflow.py:~202): a sequence of repeated no-progress probe
        cycles must leave the count at its pre-episode default throughout."""
        state = WorkflowState()
        assert state.consecutive_no_progress_count == 0

        for _ in range(5):
            WorkflowOrchestrator._record_evaluation_state(
                state, _execution(), _evaluation(meaningful_progress=False), count_toward_no_progress=False
            )

        assert state.consecutive_no_progress_count == 0

    def test_goal_directed_path_calls_increment_exactly_as_today(self):
        """Critical regression guard: the goal-directed call site's default
        (count_toward_no_progress=True, i.e. the call site passes nothing)
        must be completely unaffected by this card."""
        state = WorkflowState()

        for _ in range(5):
            WorkflowOrchestrator._record_evaluation_state(state, _execution(), _evaluation(meaningful_progress=False))

        assert state.consecutive_no_progress_count == 5

    def test_probe_path_calls_still_update_falsification_counts(self):
        """Confirms the fix didn't skip the whole call for probe cycles --
        only the one field. Falsification history from a probe click is
        real and must still accumulate."""
        state = WorkflowState()

        WorkflowOrchestrator._record_evaluation_state(
            state, _execution("ACTION6@2,2"), _evaluation(meaningful_progress=False), count_toward_no_progress=False
        )

        assert state.action_falsification_counts == {"ACTION6@2,2": 1}
        assert state.consecutive_no_progress_count == 0

    def test_mixed_probe_then_goal_directed_sequence_reproduces_live_evidence(self):
        """Reproduces this card's own live evidence (TN36: no_progress=16 at
        the first goal-directed STALL_CHECK) in a deterministic unit test:
        many probe cycles (no increment) followed by goal-directed cycles
        (real increment) -- the goal-directed count must start from a clean
        baseline unaffected by the preceding probe cycles."""
        state = WorkflowState()

        for _ in range(18):  # this session's typical 15-21 probe-cycle range
            WorkflowOrchestrator._record_evaluation_state(
                state, _execution(), _evaluation(meaningful_progress=False), count_toward_no_progress=False
            )
        assert state.consecutive_no_progress_count == 0, "probe phase must leave goal-directed play's starting count at 0, not 18"

        for _ in range(3):
            WorkflowOrchestrator._record_evaluation_state(state, _execution(), _evaluation(meaningful_progress=False))

        assert state.consecutive_no_progress_count == 3, "goal-directed cycles increment normally from the clean baseline"


# --- Group 3: the A241 interaction -- a real resumed probe window ---


def _entity(entity_ref: int) -> PerceivedEntity:
    return PerceivedEntity(kind="point", value=str(entity_ref), attributes={"entity_ref": entity_ref})


def _make_resume_dependencies(*, readiness_statuses, normal_annatar_decisions, probe_annatar_decisions):
    """Mirrors test_a241_readiness_remap_on_total_failure.py's own
    `_make_resume_integration_dependencies` fixture shape exactly (same
    readiness_gate closure convention: NOT_READY carries a probe_candidate,
    everything else doesn't) -- this file adds an `evaluate` that always
    reports no progress and an `annatar` mock that records
    state.consecutive_no_progress_count at the moment it's called (which is
    always AFTER that cycle's own _record_evaluation_state call, for both
    the probe-path and goal-directed call sites), so the test can assert
    the count's value cycle-by-cycle."""
    goal = ResolvedGoal(selected=GoalHypothesis(goal_id="g1", description="d", confidence=0.5))
    calls = {"readiness_gate": 0, "normal_annatar": 0, "probe_annatar": 0}
    counts_seen: list[int] = []

    def perceive(state, observation):
        return PhaseResult(
            phase=WorkflowPhase.PERCEIVE, status=PhaseStatus.OK,
            payload=PerceptionSnapshot(observation={}, grid_hash="h1", entities=(_entity(1),)),
        )

    def readiness_gate(state, perception):
        idx = min(calls["readiness_gate"], len(readiness_statuses) - 1)
        calls["readiness_gate"] += 1
        status = readiness_statuses[idx]
        probe_candidate = None
        if status == ReadinessStatus.NOT_READY:
            probe_candidate = PlanCandidate(
                action_id="ACTION6", goal_id="readiness_probe", payload={"x": 1, "y": 1},
                metadata={"entity_ref": 1, "readiness_probe": True},
            )
        return PhaseResult(
            phase=WorkflowPhase.READINESS_GATE, status=PhaseStatus.OK,
            payload={
                "status": status, "entity_domains": {}, "entities_mapped": 0, "entities_total": 1,
                "probe_candidate": probe_candidate, "untested_non_click_actions": [],
            },
        )

    def resolve(state, perception):
        return PhaseResult(phase=WorkflowPhase.RESOLVE, status=PhaseStatus.OK, payload=goal)

    def plan(state, perception, resolved_goal):
        candidate = PlanCandidate(action_id="ACTION1", goal_id="g1")
        return PhaseResult(phase=WorkflowPhase.PLAN, status=PhaseStatus.OK, payload=[candidate])

    def vet(state, perception, resolved_goal, planning):
        candidate = planning[0] if planning else None
        return PhaseResult(
            phase=WorkflowPhase.VET, status=PhaseStatus.OK, payload=VetDecision(approved=True, candidate=candidate)
        )

    def execute(state, perception, resolved_goal, vet_decision):
        return PhaseResult(
            phase=WorkflowPhase.EXECUTE, status=PhaseStatus.OK,
            payload=ExecutionResult(action_id=vet_decision.candidate.action_id, candidate=vet_decision.candidate, observation={}),
        )

    def evaluate(state, perception, resolved_goal, execution):
        # Always no progress -- isolates this test to the no-progress-count
        # bookkeeping question, mirroring probe cycles' own real-world
        # "essentially never register meaningful_progress" behavior.
        return PhaseResult(
            phase=WorkflowPhase.EVALUATE, status=PhaseStatus.OK,
            payload=EvaluationResult(decision=WorkflowDecision.CONTINUE, meaningful_progress=False, metadata={"grid_changed": False}),
        )

    def annatar(state, perception, execution, evaluation, *, readiness_report=None, **_ignored):
        counts_seen.append(state.consecutive_no_progress_count)
        if readiness_report is not None:
            idx = min(calls["probe_annatar"], len(probe_annatar_decisions) - 1)
            calls["probe_annatar"] += 1
            return probe_annatar_decisions[idx]
        idx = min(calls["normal_annatar"], len(normal_annatar_decisions) - 1)
        calls["normal_annatar"] += 1
        return normal_annatar_decisions[idx]

    deps = WorkflowDependencies(
        perceive=perceive, resolve=resolve, plan=plan, vet=vet, execute=execute, evaluate=evaluate,
        annatar=annatar, readiness_gate=readiness_gate,
    )
    return deps, calls, counts_seen


class TestWorkflowResumeMappingNoProgressExclusion:
    def test_resumed_probe_window_excluded_and_pre_resume_goal_directed_count_preserved(self):
        """Cycle 1: gate reports READY immediately -- normal (goal-directed)
        path runs, records no progress (count 0 -> 1), and its own annatar
        call returns resume_mapping=True (simulating whole-episode-futility
        having just been intercepted, per A241). Cycle 2: control returns to
        the top of the outer loop and re-enters the readiness-gate `if`
        block -- this time NOT_READY with a probe_candidate, so the EXISTING
        probe-path block runs (the same one the episode's original mapping
        pass uses), records no progress via count_toward_no_progress=False
        (count must stay at 1, not increment to 2), and its own annatar call
        reports exploration_complete=False -> `continue`s back to the top of
        the outer loop (still not ready). Cycle 3: the readiness_gate `if`
        block is entered a THIRD time (readiness_statuses clamps to its last
        entry, READY) -> probe_candidate is None this time -> gate marks
        itself resolved and falls through to the normal path in the same
        iteration -> records no progress again (count 1 -> 2, proving the
        pre-resume goal-directed value was PRESERVED, not reset to 0), and
        annatar terminates.
        """
        resume_outcome = AnnatarOutcome(decision="advance", resume_mapping=True)
        terminate_outcome = AnnatarOutcome(decision="terminate")
        probe_resolves = AnnatarOutcome(decision="repeat_deepen", exploration_complete=False)

        deps, calls, counts_seen = _make_resume_dependencies(
            readiness_statuses=[ReadinessStatus.READY, ReadinessStatus.NOT_READY, ReadinessStatus.READY],
            normal_annatar_decisions=[resume_outcome, terminate_outcome],
            probe_annatar_decisions=[probe_resolves],
        )
        orchestrator = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=10))
        state = WorkflowState()

        result = orchestrator.run(state, {})

        assert result.status == WorkflowStatus.TERMINATED
        assert calls["readiness_gate"] == 3, "proof control really re-entered the probe-path readiness check after the resume"
        assert calls["probe_annatar"] == 1, "exactly one resumed probe cycle ran"
        assert counts_seen == [1, 1, 2], (
            "cycle 1 (goal-directed): 0 -> 1. cycle 2 (resumed probe): stays at 1, excluded automatically because it "
            "re-enters the same probe-path code. cycle 3 (goal-directed, post-resume): 1 -> 2 -- the pre-resume "
            "goal-directed value was PRESERVED across the remap, not reset to 0 (consecutive_no_progress_count is a "
            "different signal than annatar_unproductive_anchor_streak, which A241 does reset on resume)."
        )
        assert state.consecutive_no_progress_count == 2
