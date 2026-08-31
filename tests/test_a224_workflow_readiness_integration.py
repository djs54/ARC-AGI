"""Tests for A224 Task 5: wiring the Cynefin readiness gate into
workflow.py's phase sequence -- the highest-risk piece of the whole card,
touching the most load-bearing file in the runtime.

Covers, in order:
1. classify_entity_domain / classify_all_entity_domains (annatar_signals.py)
   -- new shared helpers, extracted so the whole-perception readiness check
   doesn't duplicate A218's fetch_entity_history-boost logic a third time
   (plan_generator.py's Task 2 addition already has its own inline copy;
   not retrofitted here, out of scope for this task -- see A224's Outcome).
2. WorkflowOrchestrator routing via a NEW `readiness_gate` dependency
   (WorkflowDependencies has no graph_port of its own -- it's only ever
   captured via closures at bundle-construction time, same pattern as
   resolve/plan/execute -- confirmed by reading ports.py directly rather
   than assumed). None means "no readiness gate, run exactly as today",
   same backward-compat convention `annatar: AnnatarPhase | None` already
   established. NOT_READY (with a selectable probe_candidate) -> probe path
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

from agents.arc4.annatar_signals import classify_all_entity_domains, classify_entity_domain
from agents.arc4.annatar_state_machine import CynefinDomain, ReadinessStatus
from agents.arc4.types import (
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


def _make_dependencies(*, graph_port, resolve_calls, execute_calls):
    goal = ResolvedGoal(selected=GoalHypothesis(goal_id="g1", description="d", confidence=0.5))

    def perceive(state, observation):
        return _perception_result(observation.get("entities", ()))

    def readiness_gate(state, perception):
        domains = classify_all_entity_domains(perception, graph_port)
        entities_total = len(domains)
        entities_mapped = sum(1 for d in domains.values() if d != CynefinDomain.DISORDER)
        status = ReadinessStatus.READY if entities_mapped == entities_total else ReadinessStatus.NOT_READY
        probe_candidate = None
        if status == ReadinessStatus.NOT_READY:
            probe_candidate = PlanGenerator()._select_readiness_probe(perception, domains)
        return PhaseResult(
            phase=WorkflowPhase.READINESS_GATE,
            status=PhaseStatus.OK,
            payload={
                "status": status,
                "entity_domains": domains,
                "entities_mapped": entities_mapped,
                "entities_total": entities_total,
                "probe_candidate": probe_candidate,
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
        annatar=None,
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
        """None means "no readiness gate, run exactly as today" -- same
        backward-compat convention `annatar: AnnatarPhase | None` already
        established. Existing callers that don't wire this in must be
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
        assert execute_calls[0].goal_id == "g1"
