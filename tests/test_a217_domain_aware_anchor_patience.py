"""A217: domain-aware anchor deepening patience (Cynefin v1 slice).

Covers classify_domain() (pure, zero-I/O), transition()'s domain-scaled
DEEPENING escalation, and compute_cycle_signals()'s wiring of the domain
field from the same fetch_entity_neighborhood evidence already fetched
there. See backlog/A217.md for the full settled design and
backlog/plans/A-217-domain-aware-anchor-patience.md for the step-by-step
plan this test file implements (Steps 3, 6, and 7).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from agents.arc4.annatar_state_machine import CynefinDomain, CycleSignals, InvestigationState, transition, classify_domain
from agents.arc4.annatar_signals import compute_cycle_signals
from agents.arc4.types import EvaluationResult, ExecutionResult, PerceptionSnapshot, PlanCandidate, WorkflowDecision, WorkflowState


def _perception_snapshot(grid_hash: str = "h1") -> PerceptionSnapshot:
    return PerceptionSnapshot(observation={"grid": grid_hash}, grid_hash=grid_hash)


def _execution_result(action_id: str = "a1", candidate: PlanCandidate | None = None) -> ExecutionResult:
    if candidate is None:
        candidate = PlanCandidate(action_id=action_id, goal_id="g1")
    return ExecutionResult(action_id=action_id, candidate=candidate, observation={"grid": "h2"})


def _evaluation_result(*, meaningful_progress: bool, grid_changed: bool) -> EvaluationResult:
    return EvaluationResult(
        decision=WorkflowDecision.CONTINUE,
        meaningful_progress=meaningful_progress,
        metadata={"grid_changed": grid_changed},
    )


class TestClassifyDomain:
    def test_no_evidence_is_disorder(self):
        assert classify_domain([]) == CynefinDomain.DISORDER

    def test_all_falsified_is_chaotic(self):
        evidence = [
            {"falsified": True, "to_color": 5, "confidence": 0.0},
            {"falsified": True, "to_color": 3, "confidence": 0.0},
        ]
        assert classify_domain(evidence) == CynefinDomain.CHAOTIC

    def test_live_evidence_agreeing_is_converged(self):
        evidence = [
            {"falsified": False, "to_color": 5, "confidence": 0.6},
            {"falsified": False, "to_color": 5, "confidence": 0.4},
            {"falsified": True, "to_color": 2, "confidence": 0.0},  # falsified, ignored
        ]
        assert classify_domain(evidence) == CynefinDomain.CONVERGED

    def test_live_evidence_disagreeing_is_complex(self):
        evidence = [
            {"falsified": False, "to_color": 5, "confidence": 0.5},
            {"falsified": False, "to_color": 3, "confidence": 0.5},
        ]
        assert classify_domain(evidence) == CynefinDomain.COMPLEX

    def test_single_live_item_is_converged_not_complex(self):
        """One live item can't disagree with itself -- degenerate agreement case."""
        assert classify_domain([{"falsified": False, "to_color": 5, "confidence": 0.5}]) == CynefinDomain.CONVERGED

    def test_missing_falsified_key_defaults_to_live(self):
        """Real fetch_entity_neighborhood items may omit the key entirely for a fresh item -- must not crash, must not be treated as falsified."""
        evidence = [{"to_color": 5, "confidence": 0.3}]
        assert classify_domain(evidence) == CynefinDomain.CONVERGED

    def test_missing_to_color_key_does_not_crash(self):
        """A hypothesis-shaped item may not have to_color at all (that's a Rule-specific field) -- must not crash, treat as its own distinct (None) outcome."""
        evidence = [{"falsified": False, "confidence": 0.3}]
        result = classify_domain(evidence)
        assert result in (CynefinDomain.CONVERGED, CynefinDomain.COMPLEX)  # must not raise

    def test_hypothesis_items_compare_on_claim_not_to_color(self):
        """Real fetch_entity_neighborhood hypothesis items (confirmed against
        tests/test_a192_entity_neighborhood_candidate_seeding.py's fixtures)
        carry hypothesis_id/claim/confidence/falsified -- never to_color,
        which is Rule-specific (rule_extraction.py). Two live hypotheses with
        genuinely different claims must read as disagreement (COMPLEX), not
        silently collapse onto a shared None to_color bucket (which would
        misreport CONVERGED for evidence that actually disagrees)."""
        evidence = [
            {"hypothesis_id": "h1", "claim": "moves right", "confidence": 0.8, "falsified": False},
            {"hypothesis_id": "h2", "claim": "changes color", "confidence": 0.5, "falsified": False},
        ]
        assert classify_domain(evidence) == CynefinDomain.COMPLEX

    def test_hypothesis_items_agreeing_on_claim_is_converged(self):
        evidence = [
            {"hypothesis_id": "h1", "claim": "moves right", "confidence": 0.8, "falsified": False},
            {"hypothesis_id": "h2", "claim": "moves right", "confidence": 0.3, "falsified": False},
        ]
        assert classify_domain(evidence) == CynefinDomain.CONVERGED


class TestDomainAwareDeepeningPatience:
    def test_complex_domain_gets_more_deepening_cycles_before_escalation(self):
        """At deepening_cycle_count=3 (the flat default), a CONVERGED-domain
        anchor must escalate to AWAITING_LLM, but a COMPLEX-domain anchor
        with the same cycle count must NOT -- it gets more patience."""
        base_kwargs = dict(
            meaningful_progress=False, confidence=0.3, untested_remaining=True,
            all_falsified=False, execution_inconclusive=False,
            deepening_cycle_count=3, already_retried=False,
        )
        converged_signals = CycleSignals(**base_kwargs, domain=CynefinDomain.CONVERGED)
        assert transition(InvestigationState.DEEPENING, converged_signals) == InvestigationState.AWAITING_LLM

        complex_signals = CycleSignals(**base_kwargs, domain=CynefinDomain.COMPLEX)
        assert transition(InvestigationState.DEEPENING, complex_signals) == InvestigationState.DEEPENING

    def test_complex_domain_still_escalates_eventually(self):
        """Patience is extended, not infinite -- confirm it still escalates
        once deepening_cycle_count crosses the domain-scaled effective limit
        (default 3 * 2.0 = 6)."""
        signals = CycleSignals(
            meaningful_progress=False, confidence=0.3, untested_remaining=True,
            all_falsified=False, execution_inconclusive=False,
            deepening_cycle_count=6, already_retried=False, domain=CynefinDomain.COMPLEX,
        )
        assert transition(InvestigationState.DEEPENING, signals) == InvestigationState.AWAITING_LLM

    def test_chaotic_and_disorder_domains_unchanged_from_default(self):
        """Regression: only COMPLEX gets extra patience -- CHAOTIC and
        DISORDER must escalate at exactly the same cycle count as before
        this card (the flat default, 3)."""
        for domain in (CynefinDomain.CHAOTIC, CynefinDomain.DISORDER):
            signals = CycleSignals(
                meaningful_progress=False, confidence=0.3, untested_remaining=True,
                all_falsified=False, execution_inconclusive=False,
                deepening_cycle_count=3, already_retried=False, domain=domain,
            )
            assert transition(InvestigationState.DEEPENING, signals) == InvestigationState.AWAITING_LLM


class TestComputeCycleSignalsDomain:
    def test_entity_anchor_with_disagreeing_evidence_computes_complex_domain(self):
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {
            "hypotheses": [],
            "rules": [
                {"confidence": 0.6, "falsified": False, "to_color": 5},
                {"confidence": 0.4, "falsified": False, "to_color": 3},
            ],
        }
        graph_port.fetch_untested_actions.return_value = ["ACTION1"]

        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=True),
            anchor_ref="e1",
            anchor_type="entity",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=graph_port,
        )
        assert signals.domain == CynefinDomain.COMPLEX

    def test_entity_anchor_with_agreeing_evidence_computes_converged_domain(self):
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {
            "hypotheses": [],
            "rules": [
                {"confidence": 0.6, "falsified": False, "to_color": 5},
                {"confidence": 0.4, "falsified": False, "to_color": 5},
            ],
        }
        graph_port.fetch_untested_actions.return_value = ["ACTION1"]

        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=True),
            anchor_ref="e1",
            anchor_type="entity",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=graph_port,
        )
        assert signals.domain == CynefinDomain.CONVERGED

    def test_goal_anchor_defaults_domain_to_disorder(self):
        """anchor_type != 'entity' never queries fetch_entity_neighborhood --
        domain must default to DISORDER (the conservative 'we don't know'
        case), not be left unset."""
        graph_port = MagicMock()
        graph_port.fetch_untested_actions.return_value = []

        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=True),
            anchor_ref="g1",
            anchor_type="goal",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=graph_port,
        )
        assert signals.domain == CynefinDomain.DISORDER
        graph_port.fetch_entity_neighborhood.assert_not_called()

    def test_graph_port_none_defaults_domain_to_disorder(self):
        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=True),
            anchor_ref="e1",
            anchor_type="entity",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=None,
        )
        assert signals.domain == CynefinDomain.DISORDER

    def test_graph_query_exception_degrades_domain_to_disorder(self):
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.side_effect = RuntimeError("graph down")
        graph_port.fetch_untested_actions.side_effect = RuntimeError("graph down")

        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=True),
            anchor_ref="e1",
            anchor_type="entity",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=graph_port,
        )
        assert signals.domain == CynefinDomain.DISORDER
        assert signals.degraded is True
