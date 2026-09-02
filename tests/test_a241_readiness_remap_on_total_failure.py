"""Tests for A241: `PARTIAL_FALLTHROUGH` is a one-way latch -- whole-episode-
futility termination (annatar_signals.py::run_annatar_cycle's
annatar_unproductive_anchor_streak override) never reconsidered incomplete
readiness-gate mapping as a possible cause, even while
state.readiness_gate_partial sat unconsulted in state the whole time.

Test groups, matching the plan's TDD list (backlog/plans/A-241-readiness-
gate-remap-on-total-failure.md):
  - TestRunAnnatarCycleResumeMapping: the interception point itself (Step 2)
    -- AnnatarOutcome.resume_mapping fires instead of TERMINATE exactly when
    state.readiness_gate_partial is True AND a LIVE re-derivation (not the
    stale state.readiness_gate_entities_mapped/entities_total snapshot) of
    entities_mapped < entities_total still holds. The
    entities_mapped == entities_total regression guard
    (test_terminate_fires_normally_when_never_partial) is the single most
    important test in this file per the plan's own framing.
  - TestBundleReadinessGateRemapRebase: Step 4's actual hard part --
    readiness_status()'s elapsed-budget-fraction check is computed against
    TOTAL episode budget, so a naive readiness_gate_resolved reset would
    instantly re-fall-through on the very first re-check. Exercises the REAL
    arc_runtime/bundle.py::_readiness_gate closure (via build_arc_v2_bundle),
    not a test-local replica, mirroring test_a231's own established pattern
    for this exact function.
  - TestWorkflowResumeMappingIntegration: Step 3 -- WorkflowOrchestrator.run
    actually re-enters the EXISTING probe-path code (the `if probe_candidate
    is not None:` block) when Annatar signals resume_mapping, not a
    duplicate copy of it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.annatar_signals import run_annatar_cycle
from agents.arc4.annatar_state_machine import ReadinessStatus
from agents.arc4.graph_queries import ARC_V2_TOOL_NAMES
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
from arc_runtime.bundle import build_arc_v2_bundle
from arc_runtime.game_session import ArcV2GameSession


# --- Group 1: annatar_signals.py::run_annatar_cycle's interception point ---


def _perception_with_entities(entity_refs: list[Any]) -> PerceptionSnapshot:
    entities = tuple(
        PerceivedEntity(kind="point", value=str(i), attributes={"entity_ref": ref})
        for i, ref in enumerate(entity_refs)
    )
    return PerceptionSnapshot(observation={"grid": "h1"}, grid_hash="h1", entities=entities)


def _graph_port_with_entity_domains(*, anchor_entity_ref: Any, mapped_entity_refs: set, unmapped_entity_refs: set):
    """A single graph_port double that serves two independent readers within
    the same run_annatar_cycle call: compute_cycle_signals' own domain read
    for the per-anchor entity (driven to CHAOTIC via all-falsified rules, the
    same mechanism TestRunAnnatarCycleWholeEpisodeFutility in
    tests/test_a202_annatar_orchestrator_integration.py already uses to
    reach EXHAUSTED->ADVANCE), and the NEW live re-derivation this card adds
    (classify_all_entity_domains over perception.entities) that decides
    whether resume_mapping should fire."""

    def fetch_entity_neighborhood(entity_ref):
        if entity_ref == anchor_entity_ref:
            return {"hypotheses": [], "rules": [{"confidence": 0.0, "falsified": True, "to_color": 5}]}
        if entity_ref in mapped_entity_refs:
            return {"hypotheses": [], "rules": [{"confidence": 0.8, "falsified": False, "to_color": 3}]}
        return {"hypotheses": [], "rules": []}  # unmapped_entity_refs, or anything unlisted -- DISORDER

    graph_port = MagicMock()
    graph_port.fetch_entity_neighborhood.side_effect = fetch_entity_neighborhood
    graph_port.fetch_untested_actions.return_value = []
    return graph_port


def _unproductive_advance(state, perception, graph_port, *, anchor_entity_ref="anchor-e1", stall_reason="stalled"):
    """One cycle that concludes an anchor via EXHAUSTED->ADVANCE without
    ever registering meaningful_progress -- mirrors TestRunAnnatarCycle
    WholeEpisodeFutility._unproductive_advance in test_a202_annatar_
    orchestrator_integration.py exactly, except perception/graph_port are
    passed in so this file's tests can control the wider entity_domains
    picture the A241 live re-derivation reads."""
    candidate = PlanCandidate(action_id="a1", goal_id="g1", metadata={"entity_ref": anchor_entity_ref})
    execution = ExecutionResult(action_id="a1", candidate=candidate, observation={"grid": "h2"})
    evaluation = EvaluationResult(
        decision=WorkflowDecision.CONTINUE, meaningful_progress=False, metadata={"grid_changed": True}
    )
    return run_annatar_cycle(
        state, perception, execution, evaluation, graph_port=graph_port, stall_reason=stall_reason
    )


def _advance_to_threshold(state, perception, graph_port):
    """Three unproductive anchors in a row -- DEFAULT_MAX_UNPRODUCTIVE_
    ANCHORS -- returns the outcome of the third (threshold-crossing) call,
    the one that decides terminate vs. resume_mapping."""
    _unproductive_advance(state, perception, graph_port)
    _unproductive_advance(state, perception, graph_port)
    return _unproductive_advance(state, perception, graph_port)


class TestRunAnnatarCycleResumeMapping:
    def test_resume_mapping_fires_when_real_unmapped_entities_remain(self):
        perception = _perception_with_entities(["m1", "u1", "u2"])
        graph_port = _graph_port_with_entity_domains(
            anchor_entity_ref="anchor-e1", mapped_entity_refs={"m1"}, unmapped_entity_refs={"u1", "u2"}
        )
        state = WorkflowState(
            active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g7", description="d")),
            readiness_gate_partial=True,
            readiness_gate_entities_mapped=1,
            readiness_gate_entities_total=3,
            step_index=20,
        )

        outcome = _advance_to_threshold(state, perception, graph_port)

        assert outcome.decision == "advance", "decision stays ADVANCE -- resume_mapping is a separate field, not a new decision value"
        assert outcome.resume_mapping is True
        assert state.annatar_unproductive_anchor_streak == 0, "streak resets so the resumed goal-directed round gets a fresh count"
        assert state.readiness_gate_remap_started_step_index == 20

    def test_terminate_fires_when_live_recheck_shows_fully_mapped_despite_stale_partial_flag(self):
        """The staleness case (Step 1): state.readiness_gate_partial is
        still True (never reset since the original fallthrough), but every
        entity has since been resolved -- via the dedicated probe path, or
        incidentally via goal-directed play's own classify_entity_domain_
        detailed calls (plan_generator.py::_build_candidates), doesn't
        matter which. The live re-derivation must see this and refuse to
        resume, even though the stale flag alone would say yes."""
        perception = _perception_with_entities(["m1", "m2"])
        graph_port = _graph_port_with_entity_domains(
            anchor_entity_ref="anchor-e1", mapped_entity_refs={"m1", "m2"}, unmapped_entity_refs=set()
        )
        state = WorkflowState(
            active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g7", description="d")),
            readiness_gate_partial=True,
            readiness_gate_entities_mapped=1,
            readiness_gate_entities_total=2,
        )

        outcome = _advance_to_threshold(state, perception, graph_port)

        assert outcome.decision == "terminate"
        assert outcome.resume_mapping is False
        assert state.readiness_gate_remap_started_step_index is None

    def test_terminate_fires_normally_when_never_partial(self):
        """The single most important regression guard in this file (per the
        plan's own framing): a puzzle that reached full READY mapping (the
        gate never partially fell through at all) must terminate on whole-
        episode-futility exactly as it did before this card -- completely
        unaffected -- even though real unmapped entities happen to be
        present in this perception snapshot. state.readiness_gate_partial
        (never True here) is what must gate this, not the live entity
        count alone."""
        perception = _perception_with_entities(["u1"])
        graph_port = _graph_port_with_entity_domains(
            anchor_entity_ref="anchor-e1", mapped_entity_refs=set(), unmapped_entity_refs={"u1"}
        )
        state = WorkflowState(
            active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g7", description="d")),
            readiness_gate_partial=False,
        )

        outcome = _advance_to_threshold(state, perception, graph_port)

        assert outcome.decision == "terminate"
        assert outcome.resume_mapping is False
        assert state.active_investigation_anchor is None

    def test_resume_can_fire_a_second_time_if_entities_still_remain_after_first_resume(self):
        """No artificial single-use attempt cap -- entities_mapped <
        entities_total is real, monotonic, graph-grounded, and stays the
        bound across as many resumes as the real graph state warrants."""
        perception = _perception_with_entities(["m1", "u1"])
        graph_port = _graph_port_with_entity_domains(
            anchor_entity_ref="anchor-e1", mapped_entity_refs={"m1"}, unmapped_entity_refs={"u1"}
        )
        state = WorkflowState(
            active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g7", description="d")),
            readiness_gate_partial=True,
            step_index=20,
        )
        first = _advance_to_threshold(state, perception, graph_port)
        assert first.resume_mapping is True
        assert state.annatar_unproductive_anchor_streak == 0

        # Simulate the resumed probe window having run for a while and
        # (per this scenario) still not having fully closed the gap --
        # workflow.py's real readiness-gate block would have refreshed
        # state.readiness_gate_partial itself; this test sets it directly
        # to isolate run_annatar_cycle's own logic from workflow.py's.
        state.readiness_gate_partial = True
        state.step_index = 26
        second = _advance_to_threshold(state, perception, graph_port)

        assert second.decision == "advance"
        assert second.resume_mapping is True, "a second resume must be allowed -- no single-use cap"
        assert state.readiness_gate_remap_started_step_index == 26

    def test_second_terminate_fires_for_real_once_fully_mapped_after_a_first_resume(self):
        """Convergence: once the (possibly repeated) resume(s) actually
        close the mapping gap, a later whole-episode-futility crossing
        terminates for real -- the bound is reached, not an attempt count."""
        perception = _perception_with_entities(["m1", "u1"])
        graph_port = _graph_port_with_entity_domains(
            anchor_entity_ref="anchor-e1", mapped_entity_refs={"m1"}, unmapped_entity_refs={"u1"}
        )
        state = WorkflowState(
            active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id="g7", description="d")),
            readiness_gate_partial=True,
        )
        first = _advance_to_threshold(state, perception, graph_port)
        assert first.resume_mapping is True

        # The resumed probing (simulated) finished mapping the last entity.
        graph_port2 = _graph_port_with_entity_domains(
            anchor_entity_ref="anchor-e1", mapped_entity_refs={"m1", "u1"}, unmapped_entity_refs=set()
        )
        state.readiness_gate_partial = True  # still stale-True; live recheck must be what actually decides
        second = _advance_to_threshold(state, perception, graph_port2)

        assert second.decision == "terminate"
        assert second.resume_mapping is False


# --- Group 2: arc_runtime/bundle.py::_readiness_gate's budget-fraction rebase ---


@dataclass
class _FakeBrainClient:
    neighborhoods: dict[Any, dict]
    calls: list[tuple[str, dict]] = field(default_factory=list)

    def call_tool(self, name: str, payload: dict) -> dict:
        self.calls.append((name, dict(payload)))
        if name == ARC_V2_TOOL_NAMES["fetch_entity_neighborhood"]:
            return self.neighborhoods.get(payload.get("entity_ref"), {"hypotheses": [], "rules": []})
        if name == ARC_V2_TOOL_NAMES["fetch_untested_actions"]:
            return {"untested": [], "tested": []}
        return {}


class _FakeGameSession(ArcV2GameSession):
    def __init__(self) -> None:  # noqa: super-init-not-called -- test double
        pass


def _build_bundle(brain_client: _FakeBrainClient, *, max_cycles: int = 30):
    return build_arc_v2_bundle(
        task_id="arc_eval_001",
        game_id="game-a241",
        game_title="Test Game",
        game_tags=(),
        brain_client=brain_client,
        session_id="session-1",
        append_snapshot=None,
        game_session=_FakeGameSession(),
        world_model_eval=False,
        max_cycles=max_cycles,
        llm_client=None,
    )


def _bundle_perception(entity_refs: list[Any]) -> PerceptionSnapshot:
    entities = tuple(
        PerceivedEntity(kind="point", value=str(i), attributes={"entity_ref": ref})
        for i, ref in enumerate(entity_refs)
    )
    return PerceptionSnapshot(observation={}, grid_hash="h1", entities=entities)


class TestBundleReadinessGateRemapRebase:
    def test_without_remap_marker_default_fraction_stays_untouched(self):
        """No resume active (readiness_gate_remap_started_step_index is
        None, the default) -- must fall through exactly as pre-A241, even
        with step_index already well past the default 0.5 fraction. An
        episode that never resumes is completely unaffected by this card."""
        brain_client = _FakeBrainClient(neighborhoods={"u1": {"hypotheses": [], "rules": []}})
        bundle = _build_bundle(brain_client, max_cycles=30)
        perception = _bundle_perception(["u1"])
        state = WorkflowState(step_index=20)  # 20/30 = 0.667 >= 0.5
        assert state.readiness_gate_remap_started_step_index is None

        result = bundle.dependencies.readiness_gate(state, perception)

        assert result.payload["status"] == ReadinessStatus.PARTIAL_FALLTHROUGH

    def test_resume_rebases_ceiling_so_first_recheck_does_not_instantly_fallthrough(self):
        """The core Step 4 fix: a resume granted at step_index=20 (out of
        30) rebases the ceiling to REMAP_BUDGET_FRACTION (0.5) of what
        remained at that moment -- 20 + (30-20)*0.5 = 25, i.e. a fraction of
        25/30. One cycle later (step_index=21) must still be NOT_READY, not
        an instant re-fallthrough with zero net probing -- the exact bug
        the naive `readiness_gate_resolved = False` reset would have."""
        brain_client = _FakeBrainClient(neighborhoods={"u1": {"hypotheses": [], "rules": []}})
        bundle = _build_bundle(brain_client, max_cycles=30)
        perception = _bundle_perception(["u1"])
        state = WorkflowState(step_index=21, readiness_gate_remap_started_step_index=20)

        result = bundle.dependencies.readiness_gate(state, perception)

        assert result.payload["status"] == ReadinessStatus.NOT_READY

    def test_rebased_ceiling_still_falls_through_once_reached(self):
        """The rebase bounds the resumed window -- it doesn't remove the
        safety valve. Once step_index reaches the rebased ceiling (25, per
        the same 20-start/30-total/0.5-fraction arithmetic above), it must
        genuinely fall through again, reserving the remaining budget for a
        second round of goal-directed play rather than probing forever."""
        brain_client = _FakeBrainClient(neighborhoods={"u1": {"hypotheses": [], "rules": []}})
        bundle = _build_bundle(brain_client, max_cycles=30)
        perception = _bundle_perception(["u1"])
        state = WorkflowState(step_index=25, readiness_gate_remap_started_step_index=20)

        result = bundle.dependencies.readiness_gate(state, perception)

        assert result.payload["status"] == ReadinessStatus.PARTIAL_FALLTHROUGH

    def test_fully_mapped_during_resume_reports_ready_regardless_of_rebase(self):
        """If the resumed window's own probing (or incidental goal-directed
        resolution, per Step 1) closes the gap before the rebased ceiling
        is reached, the gate reports READY exactly as it always would --
        the rebase only changes the fallthrough arithmetic, never the
        READY check itself."""
        brain_client = _FakeBrainClient(
            neighborhoods={"m1": {"hypotheses": [], "rules": [{"confidence": 0.8, "falsified": False, "to_color": 3}]}}
        )
        bundle = _build_bundle(brain_client, max_cycles=30)
        perception = _bundle_perception(["m1"])
        state = WorkflowState(step_index=21, readiness_gate_remap_started_step_index=20)

        result = bundle.dependencies.readiness_gate(state, perception)

        assert result.payload["status"] == ReadinessStatus.READY


# --- Group 3: WorkflowOrchestrator.run's control-flow resume (Step 3) ---


def _entity(entity_ref: int) -> PerceivedEntity:
    return PerceivedEntity(kind="point", value=str(entity_ref), attributes={"entity_ref": entity_ref})


def _make_resume_integration_dependencies(*, readiness_statuses, annatar_decisions):
    """A241 workflow-level integration fixture -- mirrors test_a224_
    workflow_readiness_integration.py's own `_make_dependencies` pattern
    (a local readiness_gate closure shaped exactly like arc_runtime/
    bundle.py's real one, WorkflowDependencies wired directly, no mocking
    framework) rather than reusing that file's private helper across
    modules.

    readiness_statuses: one ReadinessStatus per successive readiness_gate
    call (index clamps to the last entry once exhausted).
    annatar_decisions: one AnnatarOutcome per successive NORMAL-path
    (readiness_report=None) annatar call -- the probe-path's own annatar
    calls (readiness_report is not None) always just keep probing
    (repeat_deepen, exploration_complete=False), letting the
    readiness_statuses sequence alone decide when the gate resolves."""
    goal = ResolvedGoal(selected=GoalHypothesis(goal_id="g1", description="d", confidence=0.5))
    calls = {"readiness_gate": 0, "normal_annatar": 0}
    resolve_calls: list[int] = []
    execute_calls: list[PlanCandidate] = []

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
        resolve_calls.append(1)
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
        execute_calls.append(vet_decision.candidate)
        return PhaseResult(
            phase=WorkflowPhase.EXECUTE, status=PhaseStatus.OK,
            payload=ExecutionResult(action_id=vet_decision.candidate.action_id, candidate=vet_decision.candidate, observation={}),
        )

    def evaluate(state, perception, resolved_goal, execution):
        return PhaseResult(
            phase=WorkflowPhase.EVALUATE, status=PhaseStatus.OK,
            payload=EvaluationResult(decision=WorkflowDecision.CONTINUE, meaningful_progress=False, metadata={"grid_changed": False}),
        )

    def annatar(state, perception, execution, evaluation, *, readiness_report=None, **_ignored):
        if readiness_report is not None:
            return AnnatarOutcome(decision="repeat_deepen", exploration_complete=False)
        idx = min(calls["normal_annatar"], len(annatar_decisions) - 1)
        calls["normal_annatar"] += 1
        return annatar_decisions[idx]

    deps = WorkflowDependencies(
        perceive=perceive, resolve=resolve, plan=plan, vet=vet, execute=execute, evaluate=evaluate,
        annatar=annatar, readiness_gate=readiness_gate,
    )
    return deps, calls, resolve_calls, execute_calls


class TestWorkflowResumeMappingIntegration:
    def test_resume_mapping_reenters_real_probe_path_not_a_duplicate(self):
        """Cycle 1: gate reports READY immediately, normal path runs, and
        its own annatar call returns resume_mapping=True (simulating the
        whole-episode-futility override having just been intercepted).
        Cycle 2: control must have returned to the top of the outer
        `while True:` loop and re-entered the readiness-gate `if` block --
        this time it reports NOT_READY with a probe candidate, so the
        EXISTING `if probe_candidate is not None:` block executes (proven
        by execute_calls[1] carrying the exact probe_candidate object this
        test's own readiness_gate closure constructed, with its
        readiness_probe=True marker intact -- workflow.py never touches
        that metadata, so its presence on the captured execute() call is
        direct proof the real probe-path code ran, not a re-implementation
        of it). Cycle 3: gate reports READY again, normal path resumes, and
        this time annatar really terminates."""
        resume_outcome = AnnatarOutcome(decision="advance", resume_mapping=True)
        terminate_outcome = AnnatarOutcome(decision="terminate")
        deps, calls, resolve_calls, execute_calls = _make_resume_integration_dependencies(
            readiness_statuses=[ReadinessStatus.READY, ReadinessStatus.NOT_READY, ReadinessStatus.READY],
            annatar_decisions=[resume_outcome, terminate_outcome],
        )
        orchestrator = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=10))
        state = WorkflowState()

        result = orchestrator.run(state, {})

        assert result.status == WorkflowStatus.TERMINATED
        assert calls["readiness_gate"] == 3, "the gate must be re-invoked after the resume -- proof control returned to the top of the loop"
        assert len(resolve_calls) == 2, "resolve runs cycle 1 and cycle 3 (normal path) but NOT cycle 2 (probe path skips it)"
        assert len(execute_calls) == 3
        assert execute_calls[0].action_id == "ACTION1", "cycle 1: normal path"
        assert execute_calls[1].metadata.get("readiness_probe") is True, "cycle 2: the resumed probe path's own candidate"
        assert execute_calls[1].goal_id == "readiness_probe"
        assert execute_calls[2].action_id == "ACTION1", "cycle 3: back to the normal path, this time terminating for real"
        assert state.readiness_gate_resolved is True
        assert state.readiness_gate_remap_started_step_index is None, "cleared once the resumed window concluded"


class TestSecondVetoResumeMapping:
    """A241 also touches _route_second_veto_through_annatar -- the other
    call site that reaches run_annatar_cycle's whole-episode-futility
    override (readiness_report=None, same as the normal path). Exercised
    directly (not via a full double-veto scenario) since the interception
    logic itself is identical to the normal-path site, already covered
    above -- this only needs to prove THIS call site's own resume_mapping
    handling (state reset + returning None so the caller's existing
    `continue` fires) is wired correctly."""

    def test_resume_mapping_resets_readiness_gate_resolved_and_returns_none(self):
        def annatar(state, perception, execution, evaluation, *, stall_reason=None, **_ignored):
            return AnnatarOutcome(decision="advance", resume_mapping=True)

        deps = WorkflowDependencies(
            perceive=lambda *a, **k: None, resolve=lambda *a, **k: None, plan=lambda *a, **k: None,
            vet=lambda *a, **k: None, execute=lambda *a, **k: None, evaluate=lambda *a, **k: None,
            annatar=annatar, readiness_gate=None,
        )
        orchestrator = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=10))
        state = WorkflowState(readiness_gate_resolved=True)
        perception_payload = PerceptionSnapshot(observation={}, grid_hash="h1")

        result = orchestrator._route_second_veto_through_annatar(state, perception_payload, {}, [])

        assert result is None, "None tells the caller to `continue` the outer loop, exactly like repeat_deepen/repeat_retry already do"
        assert state.readiness_gate_resolved is False
