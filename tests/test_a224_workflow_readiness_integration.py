"""Tests for A224 Task 5: wiring the Cynefin readiness gate into
workflow.py's phase sequence -- the highest-risk piece of the whole card,
touching the most load-bearing file in the runtime.

Covers, in order:
1. classify_entity_domain / classify_all_entity_domains (annatar_signals.py)
   -- shared helpers for the whole-perception readiness check. As of A226,
   classify_entity_domain is a thin wrapper over classify_entity_domain_
   detailed, the single consolidated implementation compute_cycle_signals
   and plan_generator.py's _build_candidates both also delegate to (see
   TestClassifyEntityDomainDetailed below) -- no longer three independent
   copies of the same fetch/classify/upgrade-to-CHAOTIC sequence.
2. WorkflowOrchestrator routing via a NEW `readiness_gate` dependency
   (WorkflowDependencies has no graph_port of its own -- it's only ever
   captured via closures at bundle-construction time, same pattern as
   resolve/plan/execute -- confirmed by reading ports.py directly rather
   than assumed). None means "no readiness gate, run exactly as today" --
   `readiness_gate` is still genuinely optional (unlike `annatar`, which
   A250 made mandatory once its own identical `None`-means-legacy-mode
   convention was confirmed permanently dead in production -- see
   backlog/A250.md; `readiness_gate`'s own optionality is explicitly out of
   that card's scope). NOT_READY (with a selectable probe_candidate) -> probe path
   (skip resolve/plan/vet entirely, straight to execute/evaluate); READY ->
   normal path; PARTIAL_FALLTHROUGH -> normal path + telemetry flag;
   already-resolved (cached via state.readiness_gate_resolved) -> gate
   skipped entirely on later cycles. The gate's own PhaseResult carries
   `phase=WorkflowPhase.READINESS_GATE`, a real enum member (not borrowed
   from PERCEIVE's slot).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.annatar_signals import (
    classify_all_entity_domains,
    classify_entity_domain,
    classify_entity_domain_detailed,
)
from agents.arc4.annatar_state_machine import CynefinDomain, ReadinessStatus
from agents.arc4.types import (
    AnnatarOutcome,
    EvaluationResult,
    ExecutionResult,
    PerceivedEntity,
    PerceptionSnapshot,
    PhaseResult,
    PhaseStatus,
    PlanCandidate,
    ResolvedGoal,
    GoalHypothesis,
    VetDecision,
    WorkflowDecision,
    WorkflowPhase,
    WorkflowState,
)
from agents.arc4.plan_generator import PlanGenerator
from agents.arc4.ports import WorkflowDependencies
from agents.arc4.workflow import WorkflowLimits, WorkflowOrchestrator


def _entity(entity_ref: int, **overrides) -> PerceivedEntity:
    attrs = {
        "entity_ref": entity_ref,
        "coverage": 0.05,
        "cell_count": 3,
        "centroid": (10.0, 10.0 + entity_ref),
    }
    attrs.update(overrides)
    return PerceivedEntity(kind="point", value="5", attributes=attrs)


class TestClassifyEntityDomain:
    def test_no_graph_port_stays_disorder(self):
        assert classify_entity_domain(1, None) == CynefinDomain.DISORDER

    def test_live_disagreeing_rules_are_complex(self):
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {
            "hypotheses": [],
            "rules": [
                {"confidence": 0.6, "falsified": False, "to_color": 3},
                {"confidence": 0.5, "falsified": False, "to_color": 7},
            ],
        }
        assert classify_entity_domain(1, graph_port) == CynefinDomain.COMPLEX

    def test_confirmed_inert_via_transition_history_is_chaotic(self):
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {"hypotheses": [], "rules": []}
        graph_port.fetch_entity_history.return_value = {
            "transitions": [{"step": 1}, {"step": 2}],
            "changed_count_total": 0,
        }
        assert classify_entity_domain(1, graph_port) == CynefinDomain.CHAOTIC

    def test_exception_degrades_to_disorder(self):
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.side_effect = RuntimeError("boom")
        assert classify_entity_domain(1, graph_port) == CynefinDomain.DISORDER


class TestClassifyEntityDomainDetailed:
    """A226: the consolidated implementation classify_entity_domain,
    compute_cycle_signals, and plan_generator.py's _build_candidates all now
    delegate to -- exercises the richer return shape (live_hypotheses,
    live_rules, had_any_record, degraded) those two consumers need but the
    domain-only classify_entity_domain wrapper doesn't expose."""

    def test_no_graph_port_returns_disorder_with_empty_lists(self):
        result = classify_entity_domain_detailed(1, None)
        assert result.domain == CynefinDomain.DISORDER
        assert result.live_hypotheses == []
        assert result.live_rules == []
        assert result.had_any_record is False
        assert result.degraded is False

    def test_live_and_falsified_evidence_split_correctly(self):
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {
            "hypotheses": [{"confidence": 0.4, "falsified": True}],
            "rules": [
                {"confidence": 0.6, "falsified": False, "to_color": 3},
                {"confidence": 0.5, "falsified": False, "to_color": 7},
            ],
        }
        result = classify_entity_domain_detailed(1, graph_port)
        assert result.domain == CynefinDomain.COMPLEX
        assert result.live_hypotheses == []
        assert len(result.live_rules) == 2
        assert result.had_any_record is True

    def test_all_falsified_sets_had_any_record_true_with_no_live_evidence(self):
        """The A208 hard-exclusion signal: had_any_record=True with empty
        live_hypotheses/live_rules is exactly the "graph tested this and
        found nothing that holds" case plan_generator.py excludes on."""
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {
            "hypotheses": [{"confidence": 0.4, "falsified": True}],
            "rules": [],
        }
        result = classify_entity_domain_detailed(1, graph_port)
        assert result.had_any_record is True
        assert result.live_hypotheses == []
        assert result.live_rules == []

    def test_no_record_at_all_leaves_had_any_record_false(self):
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {"hypotheses": [], "rules": []}
        graph_port.fetch_entity_history.return_value = {"transitions": [], "changed_count_total": 0}
        result = classify_entity_domain_detailed(1, graph_port)
        assert result.had_any_record is False

    def test_confirmed_inert_via_transition_history_is_chaotic(self):
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {"hypotheses": [], "rules": []}
        graph_port.fetch_entity_history.return_value = {
            "transitions": [{"step": 1}, {"step": 2}],
            "changed_count_total": 0,
        }
        result = classify_entity_domain_detailed(1, graph_port)
        assert result.domain == CynefinDomain.CHAOTIC

    def test_neighborhood_exception_sets_degraded(self):
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.side_effect = RuntimeError("boom")
        result = classify_entity_domain_detailed(1, graph_port)
        assert result.domain == CynefinDomain.DISORDER
        assert result.degraded is True

    def test_history_exception_sets_degraded(self):
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {"hypotheses": [], "rules": []}
        graph_port.fetch_entity_history.side_effect = RuntimeError("boom")
        result = classify_entity_domain_detailed(1, graph_port)
        assert result.domain == CynefinDomain.DISORDER
        assert result.degraded is True

    def test_classify_entity_domain_wrapper_matches_detailed_domain(self):
        """classify_entity_domain must stay a pure passthrough -- same
        domain value as classify_entity_domain_detailed(...).domain for any
        given graph_port/entity_ref."""
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {
            "hypotheses": [],
            "rules": [{"confidence": 0.6, "falsified": False, "to_color": 3}],
        }
        assert classify_entity_domain(1, graph_port) == classify_entity_domain_detailed(1, graph_port).domain


class TestClassifyAllEntityDomains:
    def test_maps_every_entity_with_a_ref(self):
        perception = PerceptionSnapshot(
            observation={}, grid_hash="h1", entities=(_entity(1), _entity(2)),
        )
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {"hypotheses": [], "rules": []}
        graph_port.fetch_entity_history.return_value = {"transitions": [], "changed_count_total": 0}

        result = classify_all_entity_domains(perception, graph_port)

        assert set(result.keys()) == {1, 2}
        assert all(domain == CynefinDomain.DISORDER for domain in result.values())

    def test_entity_without_ref_is_skipped(self):
        perception = PerceptionSnapshot(
            observation={}, grid_hash="h1",
            entities=(PerceivedEntity(kind="point", value="5", attributes={}),),
        )
        result = classify_all_entity_domains(perception, MagicMock())
        assert result == {}


# --- Integration: workflow.py routing via the new readiness_gate dependency ---

def _perception_result(entities):
    return PhaseResult(
        phase=WorkflowPhase.PERCEIVE,
        status=PhaseStatus.OK,
        payload=PerceptionSnapshot(observation={}, grid_hash="h1", entities=entities),
    )


def _default_fake_annatar(state, perception, execution, evaluation, *, readiness_report=None, **_ignored):
    """A250: `annatar` is a required WorkflowDependencies field now that
    it's unconditionally wired in production (since A202) -- this is the
    minimal non-terminating, non-exploration-complete stand-in used by
    default below, so tests that aren't specifically exercising Annatar's
    own decision logic don't need to supply their own mock."""
    return AnnatarOutcome(decision="advance", exploration_complete=None)


def _make_dependencies(*, graph_port, resolve_calls, execute_calls, annatar=_default_fake_annatar, untested_non_click_actions=()):
    goal = ResolvedGoal(selected=GoalHypothesis(goal_id="g1", description="d", confidence=0.5))

    def perceive(state, observation):
        return _perception_result(observation.get("entities", ()))

    def readiness_gate(state, perception):
        domains = classify_all_entity_domains(perception, graph_port)
        entities_total = len(domains)
        entities_mapped = sum(1 for d in domains.values() if d != CynefinDomain.DISORDER)
        # A231: mirrors arc_runtime/bundle.py::_readiness_gate's real
        # wiring -- untested_non_click_actions (whole-action-space
        # coverage, fetch_untested_actions/A135) keeps the gate NOT_READY
        # even once every entity is mapped, and drives probe selection via
        # the same _select_readiness_probe the entity path already uses.
        entities_ready = entities_mapped == entities_total
        status = (
            ReadinessStatus.READY
            if entities_ready and not untested_non_click_actions
            else ReadinessStatus.NOT_READY
        )
        probe_candidate = None
        if status == ReadinessStatus.NOT_READY:
            probe_candidate = PlanGenerator()._select_readiness_probe(
                perception, domains, untested_non_click_actions=untested_non_click_actions,
            )
        return PhaseResult(
            phase=WorkflowPhase.READINESS_GATE,
            status=PhaseStatus.OK,
            payload={
                "status": status,
                "entity_domains": domains,
                "entities_mapped": entities_mapped,
                "entities_total": entities_total,
                "probe_candidate": probe_candidate,
                "untested_non_click_actions": list(untested_non_click_actions),
            },
        )

    def resolve(state, perception):
        resolve_calls.append(1)
        return PhaseResult(phase=WorkflowPhase.RESOLVE, status=PhaseStatus.OK, payload=goal)

    def plan(state, perception, resolved_goal):
        candidate = PlanCandidate(action_id="ACTION6", goal_id="g1", payload={"x": 1, "y": 1})
        return PhaseResult(phase=WorkflowPhase.PLAN, status=PhaseStatus.OK, payload=[candidate])

    def vet(state, perception, resolved_goal, planning):
        candidate = planning[0] if planning else None
        return PhaseResult(phase=WorkflowPhase.VET, status=PhaseStatus.OK, payload=VetDecision(approved=True, candidate=candidate))

    def execute(state, perception, resolved_goal, vet_decision):
        execute_calls.append(vet_decision.candidate)
        return PhaseResult(
            phase=WorkflowPhase.EXECUTE, status=PhaseStatus.OK,
            payload=ExecutionResult(action_id=vet_decision.candidate.action_id, candidate=vet_decision.candidate, observation={}, did_progress=False),
        )

    def evaluate(state, perception, resolved_goal, execution):
        return PhaseResult(
            phase=WorkflowPhase.EVALUATE, status=PhaseStatus.OK,
            payload=EvaluationResult(decision=WorkflowDecision.CONTINUE, meaningful_progress=False),
        )

    return WorkflowDependencies(
        perceive=perceive,
        resolve=resolve,
        plan=plan,
        vet=vet,
        execute=execute,
        evaluate=evaluate,
        annatar=annatar,
        readiness_gate=readiness_gate,
    )


class TestWorkflowReadinessGateRouting:
    def test_not_ready_skips_resolve_plan_vet_routes_straight_to_execute(self):
        """A single DISORDER entity, fresh episode -- the gate should route
        to the probe path, never calling resolve/plan/vet at all."""
        resolve_calls, execute_calls = [], []
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {"hypotheses": [], "rules": []}
        graph_port.fetch_entity_history.return_value = {"transitions": [], "changed_count_total": 0}
        deps = _make_dependencies(graph_port=graph_port, resolve_calls=resolve_calls, execute_calls=execute_calls)
        orchestrator = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=1))
        state = WorkflowState()

        orchestrator.run(state, {"entities": (_entity(1),)})

        assert resolve_calls == [], "resolve must not be called during the probe path"
        assert len(execute_calls) == 1
        assert execute_calls[0].metadata.get("readiness_probe") is True

    def test_ready_uses_normal_resolve_plan_vet_execute_path(self):
        """No DISORDER entities -- proceeds exactly as before this card."""
        resolve_calls, execute_calls = [], []
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {
            "hypotheses": [], "rules": [{"confidence": 0.6, "falsified": False, "to_color": 3}],
        }
        deps = _make_dependencies(graph_port=graph_port, resolve_calls=resolve_calls, execute_calls=execute_calls)
        orchestrator = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=1))
        state = WorkflowState()

        orchestrator.run(state, {"entities": (_entity(1),)})

        assert len(resolve_calls) == 1
        assert len(execute_calls) == 1
        assert execute_calls[0].goal_id == "g1", "normal path candidate, not a readiness probe"

    def test_no_readiness_gate_configured_behaves_exactly_as_before(self):
        """None means "no readiness gate, run exactly as today" --
        `readiness_gate` stays genuinely optional (see the module docstring
        for why this is a different case from `annatar`, which A250 made
        mandatory). Existing callers that don't wire this in must be
        completely unaffected."""
        resolve_calls, execute_calls = [], []
        graph_port = MagicMock()
        deps = _make_dependencies(graph_port=graph_port, resolve_calls=resolve_calls, execute_calls=execute_calls)
        deps.readiness_gate = None
        orchestrator = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=1))
        state = WorkflowState()

        orchestrator.run(state, {"entities": (_entity(1),)})

        assert len(resolve_calls) == 1, "no gate configured -- must proceed exactly as before this card"
        assert len(execute_calls) == 1

    # A250 note: this class used to also carry
    # test_readiness_gate_configured_annatar_none_behaves_exactly_as_before,
    # constructing dependencies with an explicit `annatar=None` and
    # asserting the probe path still skips resolve/plan/vet. On inspection
    # (A250's Step 1 enumeration) its assertions never actually depended on
    # whether Annatar was configured -- skipping resolve/plan/vet during the
    # probe path is a structural property of the probe path itself (the
    # `if probe_candidate is not None:` block never calls resolve/plan/vet
    # either way; only what happens AFTER execute/evaluate differs). Once
    # `_make_dependencies`'s default `annatar` became a real (non-None)
    # fake per A250 (required field), this test became a verbatim duplicate
    # of test_not_ready_skips_resolve_plan_vet_routes_straight_to_execute
    # above (identical graph_port setup, identical assertions), so it was
    # deleted as redundant rather than ported forward.

    def test_probe_cycle_calls_annatar_dependency(self):
        """A230's core regression: today this sees 0 calls -- the whole
        point of this card is that every probe cycle routes through the
        same self._dependencies.annatar(...) call site the normal path
        already uses."""
        resolve_calls, execute_calls = [], []
        annatar_calls: list[dict] = []

        def fake_annatar(state, perception, execution, evaluation, *, readiness_report=None, **_ignored):
            annatar_calls.append({"readiness_report": readiness_report})
            return AnnatarOutcome(decision="repeat_deepen", exploration_complete=False)

        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {"hypotheses": [], "rules": []}
        graph_port.fetch_entity_history.return_value = {"transitions": [], "changed_count_total": 0}
        deps = _make_dependencies(
            graph_port=graph_port, resolve_calls=resolve_calls, execute_calls=execute_calls, annatar=fake_annatar
        )
        orchestrator = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=1))
        state = WorkflowState()

        orchestrator.run(state, {"entities": (_entity(1),)})

        # Filter to calls carrying a readiness_report -- the probe-path's
        # own signature -- since _route_budget_through_annatar (unrelated,
        # pre-existing behavior) also calls annatar once budget is
        # exhausted, with readiness_report=None. The core regression this
        # card fixes is that the PROBE cycle itself now reaches annatar at
        # all -- today (pre-A230) that count is 0.
        probe_calls = [c for c in annatar_calls if c["readiness_report"] is not None]
        assert len(probe_calls) == 1, "annatar must be invoked exactly once for this single probe cycle"
        assert probe_calls[0]["readiness_report"].get("status") == ReadinessStatus.NOT_READY

    def test_annatar_exploration_complete_true_stops_probing_even_if_readiness_status_says_not_ready(self):
        """A230's actual authority-transfer proof: a fake annatar dependency
        that returns exploration_complete=True regardless of input must make
        the orchestrator stop probing and resolve the gate -- even though
        this scenario's real readiness_status() would say NOT_READY (3
        DISORDER entities, only one gets probed). Annatar's outcome, not
        readiness_status()'s raw return value, is what drives the loop."""
        resolve_calls, execute_calls = [], []
        annatar_calls: list[dict] = []

        def fake_annatar(state, perception, execution, evaluation, *, readiness_report=None, **_ignored):
            annatar_calls.append({"readiness_report": readiness_report})
            return AnnatarOutcome(decision="repeat_deepen", exploration_complete=True)

        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {"hypotheses": [], "rules": []}
        graph_port.fetch_entity_history.return_value = {"transitions": [], "changed_count_total": 0}
        deps = _make_dependencies(
            graph_port=graph_port, resolve_calls=resolve_calls, execute_calls=execute_calls, annatar=fake_annatar
        )
        # max_cycles=1: if the fall-through were instead a `continue` to a
        # SEPARATE cycle (as an alternative, also-acceptable design per the
        # plan), check_budget would end the run before that second cycle's
        # resolve ever ran, and resolve_calls would stay empty. Getting
        # resolve_calls == 1 here is the concrete proof the SAME cycle that
        # resolved the gate also reached resolve/plan/vet/execute/evaluate,
        # not a wasted extra cycle.
        orchestrator = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=1))
        state = WorkflowState()

        orchestrator.run(state, {"entities": (_entity(1), _entity(2), _entity(3))})

        # Real readiness_status() over 3 untouched DISORDER entities would
        # still say NOT_READY after probing just one of them -- but Annatar
        # said exploration_complete=True, so the gate must resolve and the
        # SAME cycle must fall through into the normal resolve/plan/vet path
        # immediately (no wasted extra cycle). Only the FIRST annatar call
        # carries a readiness_report (the probe cycle); a later call (if
        # any) is the normal per-cycle Annatar call (readiness_report=None),
        # unrelated to this proof.
        probe_calls = [c for c in annatar_calls if c["readiness_report"] is not None]
        assert len(probe_calls) == 1, "only one probe cycle should run before the gate resolves"
        assert probe_calls[0]["readiness_report"].get("status") == ReadinessStatus.NOT_READY
        assert state.readiness_gate_resolved is True
        assert len(resolve_calls) == 1, "must fall through to resolve/plan/vet in the SAME cycle Annatar resolved it"

    def test_untested_non_click_action_keeps_gate_not_ready_even_after_every_entity_mapped(self):
        """A231's core regression: readiness_status() must not report READY
        just because every visible entity got click-probed -- whole-
        action-space coverage (fetch_untested_actions/A135) is a second,
        independent condition. The probe path routes the untested action
        through the exact same execute/evaluate/Annatar cycle A230 already
        wired up for entity probes -- zero changes to workflow.py itself,
        confirmed here by using the same orchestrator/dependencies wiring
        every other test in this class uses."""
        resolve_calls, execute_calls = [], []
        annatar_calls: list[dict] = []

        def fake_annatar(state, perception, execution, evaluation, *, readiness_report=None, **_ignored):
            annatar_calls.append({"readiness_report": readiness_report})
            return AnnatarOutcome(decision="repeat_deepen", exploration_complete=False)

        # Entity already fully mapped (CONVERGED, not DISORDER) -- the
        # pre-A231 gate would have reported READY here.
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {
            "hypotheses": [], "rules": [{"confidence": 0.6, "falsified": False, "to_color": 3}],
        }
        deps = _make_dependencies(
            graph_port=graph_port,
            resolve_calls=resolve_calls,
            execute_calls=execute_calls,
            annatar=fake_annatar,
            untested_non_click_actions=["ACTION3"],
        )
        orchestrator = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=1))
        state = WorkflowState()

        orchestrator.run(state, {"entities": (_entity(1),)})

        assert resolve_calls == [], "gate must still route to the probe path, not resolve -- an untested action remains"
        assert len(execute_calls) == 1
        probed_candidate = execute_calls[0]
        assert probed_candidate.action_id == "ACTION3", "the untested action itself is the probe candidate"
        assert probed_candidate.payload == {}, "no x/y -- non-click action probes carry no coordinate"
        assert probed_candidate.metadata.get("readiness_probe") is True
        assert probed_candidate.metadata.get("readiness_probe_kind") == "action"

        probe_calls = [c for c in annatar_calls if c["readiness_report"] is not None]
        assert len(probe_calls) == 1, "annatar must be invoked for this probe cycle, same A230-routed path as entity probes"
        assert probe_calls[0]["readiness_report"].get("status") == ReadinessStatus.NOT_READY
        assert probe_calls[0]["readiness_report"].get("untested_non_click_actions") == ["ACTION3"]
