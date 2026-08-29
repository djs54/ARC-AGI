"""A218: Audit follow-up to A213/A217 -- give classify_domain() visibility
into "confirmed inert" (repeatedly tried, zero visible effect) without
reviving A213's rejected record_rule no-op payload.

Two-part fix, both ARC-side, no hippocampy schema change:

1. graph_queries.py::record_transition -- on the no-op path (empty
   changed_cells), entity_ref used to be hardcoded None because
   _attribute_entity's only mechanism is bbox-overlap-with-changed-cells,
   which is empty by definition when nothing changed. Falls back to the
   click candidate's own known target (plan_generator.py stamps entity_ref
   onto ACTION6 click-target metadata) so a repeatedly-clicked, confirmed-
   inert entity accumulates real Transition history under its own
   entity_ref instead of "none".

2. annatar_signals.py::compute_cycle_signals -- when rule/hypothesis
   evidence (fetch_entity_neighborhood) is structurally empty (DISORDER),
   a second, already-implemented, entity_ref-keyed graph read
   (fetch_entity_history, A176) is consulted. Two or more zero-effect
   Transition records with zero total changed_count reclassifies the
   domain from DISORDER ("no evidence yet") to CHAOTIC ("evidence exists,
   nothing survived as a governing constraint") -- reusing the plan's
   preferred existing CynefinDomain value rather than adding a fifth.

See backlog/A218.md for the full investigation and backlog/A213.md /
backlog/A217.md for the prior findings this follows up on.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.arc4.annatar_state_machine import CynefinDomain
from agents.arc4.annatar_signals import compute_cycle_signals
from agents.arc4.graph_queries import ArcGraphQueryPort
from agents.arc4.types import EvaluationResult, ExecutionResult, PerceptionSnapshot, PlanCandidate, WorkflowDecision, WorkflowState


def _perception_snapshot(grid_hash: str = "h1") -> PerceptionSnapshot:
    return PerceptionSnapshot(observation={"grid": grid_hash}, grid_hash=grid_hash)


def _execution_result(action_id: str = "ACTION6", candidate: PlanCandidate | None = None) -> ExecutionResult:
    if candidate is None:
        candidate = PlanCandidate(action_id=action_id, goal_id="g1")
    return ExecutionResult(action_id=action_id, candidate=candidate, observation={"grid": "h2"})


def _evaluation_result(*, meaningful_progress: bool, grid_changed: bool) -> EvaluationResult:
    return EvaluationResult(
        decision=WorkflowDecision.CONTINUE,
        meaningful_progress=meaningful_progress,
        metadata={"grid_changed": grid_changed},
    )


# ── Part 1: record_transition's no-op entity attribution fallback ──────


@pytest.fixture
def mock_brain_client():
    client = MagicMock()
    client.call_tool.return_value = {"ok": True, "transition_id": "tid"}
    return client


@pytest.fixture
def graph_port(mock_brain_client):
    return ArcGraphQueryPort(brain_client=mock_brain_client, task_id="test_task", session_id="test_session", strict=False)


class TestRecordTransitionNoOpEntityAttribution:
    def test_no_op_action_attributes_to_click_targets_own_entity_ref(self, graph_port, mock_brain_client):
        """A218: entity_ref present on the candidate's own metadata (the
        entity a click targeted) survives onto the no-op Transition record,
        instead of being hardcoded None."""
        candidate = PlanCandidate(action_id="ACTION6", goal_id="g1", metadata={"entity_ref": 18})
        execution = ExecutionResult(action_id="ACTION6@20,58", candidate=candidate, observation={})

        graph_port.record_transition(execution, {"changed_cells": []})

        payload = mock_brain_client.call_tool.call_args[0][1]
        assert payload["entity_ref"] == 18
        assert payload["changed_count"] == 0
        assert payload["color_transitions"] == []

    def test_no_op_action_without_candidate_entity_ref_stays_none(self, graph_port, mock_brain_client):
        """A218 regression: a candidate with no entity_ref in its metadata
        (a non-click action, e.g. ACTION1) still records entity_ref=None on
        the no-op path -- no fabricated attribution."""
        candidate = PlanCandidate(action_id="ACTION1", goal_id="g1")
        execution = ExecutionResult(action_id="ACTION1", candidate=candidate, observation={})

        graph_port.record_transition(execution, {"changed_cells": []})

        payload = mock_brain_client.call_tool.call_args[0][1]
        assert payload["entity_ref"] is None

    def test_no_op_action_without_candidate_stays_none(self, graph_port, mock_brain_client):
        """A213 regression (unchanged): candidate=None still records
        entity_ref=None on the no-op path."""
        execution = ExecutionResult(action_id="ACTION1", candidate=None, observation={})

        graph_port.record_transition(execution, {"changed_cells": []})

        payload = mock_brain_client.call_tool.call_args[0][1]
        assert payload["entity_ref"] is None

    def test_real_change_still_uses_bbox_attribution_not_candidate_fallback(self, graph_port, mock_brain_client):
        """A213/A176 regression: when changed_cells is non-empty, entity_ref
        attribution is unaffected by A218's no-op fallback -- it still comes
        from _attribute_entity's bbox-overlap logic, even if the candidate's
        own metadata carries a different entity_ref."""
        candidate = PlanCandidate(action_id="ACTION6", goal_id="g1", metadata={"entity_ref": 99})
        execution = ExecutionResult(action_id="ACTION6@1,1", candidate=candidate, observation={})
        entity = MagicMock()
        entity.attributes = {"bbox": (0, 0, 2, 2), "entity_ref": 7}
        grid_diff = {"changed_cells": [{"row": 1, "col": 1, "from": 1, "to": 2}], "changed_count": 1}

        graph_port.record_transition(execution, grid_diff, entities=[entity])

        payload = mock_brain_client.call_tool.call_args[0][1]
        assert payload["entity_ref"] == 7  # bbox-attributed, not the candidate's own 99


# ── Part 2: compute_cycle_signals' DISORDER -> CHAOTIC reclassification ──


class TestComputeCycleSignalsConfirmedInert:
    def _disorder_neighborhood(self):
        return {"hypotheses": [], "rules": [], "mechanics": []}

    def test_repeated_zero_effect_transitions_reclassify_disorder_to_chaotic(self):
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = self._disorder_neighborhood()
        graph_port.fetch_entity_history.return_value = {
            "transitions": [{"action_id": "ACTION6@20,58", "step": 1}, {"action_id": "ACTION6@20,58", "step": 2}],
            "changed_count_total": 0,
        }
        graph_port.fetch_untested_actions.return_value = ["ACTION1"]

        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=False),
            anchor_ref=18,
            anchor_type="entity",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=graph_port,
        )
        assert signals.domain == CynefinDomain.CHAOTIC
        graph_port.fetch_entity_history.assert_called_once_with(18)

    def test_single_zero_effect_transition_stays_disorder(self):
        """Below the repeated-not-single threshold (mirrors plan_generator
        .py's own falsifications >= 2 'repeated_falsified' convention)."""
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = self._disorder_neighborhood()
        graph_port.fetch_entity_history.return_value = {
            "transitions": [{"action_id": "ACTION6@20,58", "step": 1}],
            "changed_count_total": 0,
        }
        graph_port.fetch_untested_actions.return_value = []

        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=False),
            anchor_ref=18,
            anchor_type="entity",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=graph_port,
        )
        assert signals.domain == CynefinDomain.DISORDER

    def test_never_attempted_entity_stays_disorder(self):
        """Empty transitions (never tried) must not be conflated with
        confirmed-inert (tried repeatedly, zero effect)."""
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = self._disorder_neighborhood()
        graph_port.fetch_entity_history.return_value = {"transitions": [], "changed_count_total": 0}
        graph_port.fetch_untested_actions.return_value = []

        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=False),
            anchor_ref=18,
            anchor_type="entity",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=graph_port,
        )
        assert signals.domain == CynefinDomain.DISORDER

    def test_repeated_transitions_with_real_effect_stay_disorder(self):
        """changed_count_total > 0 means a real change happened at some
        point -- not confirmed inert, even if rule/hypothesis evidence is
        (for whatever reason) still empty."""
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = self._disorder_neighborhood()
        graph_port.fetch_entity_history.return_value = {
            "transitions": [{"action_id": "ACTION6@20,58", "step": 1}, {"action_id": "ACTION6@20,58", "step": 2}],
            "changed_count_total": 3,
        }
        graph_port.fetch_untested_actions.return_value = []

        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=False),
            anchor_ref=18,
            anchor_type="entity",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=graph_port,
        )
        assert signals.domain == CynefinDomain.DISORDER

    def test_fetch_entity_history_exception_degrades_to_disorder(self):
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = self._disorder_neighborhood()
        graph_port.fetch_entity_history.side_effect = RuntimeError("graph down")
        graph_port.fetch_untested_actions.return_value = []

        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=False),
            anchor_ref=18,
            anchor_type="entity",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=graph_port,
        )
        assert signals.domain == CynefinDomain.DISORDER
        assert signals.degraded is True

    def test_fetch_entity_history_unavailable_on_port_stays_disorder(self):
        """A port that doesn't implement fetch_entity_history at all (an
        older test double, or a real port before this capability lands)
        degrades cleanly -- no crash, no change from A217's behavior."""

        class NeighborhoodOnlyPort:
            def fetch_entity_neighborhood(self, entity_ref):
                return {"hypotheses": [], "rules": []}

            def fetch_untested_actions(self):
                return []

        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=False),
            anchor_ref=18,
            anchor_type="entity",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=NeighborhoodOnlyPort(),
        )
        assert signals.domain == CynefinDomain.DISORDER
        assert signals.degraded is False

    def test_non_disorder_domain_does_not_query_entity_history(self):
        """CONVERGED/COMPLEX/CHAOTIC already have real rule/hypothesis
        evidence -- fetch_entity_history is specifically for resolving
        DISORDER's ambiguity and must not fire (extra round-trip) when
        that ambiguity doesn't exist."""
        graph_port = MagicMock()
        graph_port.fetch_entity_neighborhood.return_value = {
            "hypotheses": [],
            "rules": [{"confidence": 0.6, "falsified": False, "to_color": 5}],
        }
        graph_port.fetch_untested_actions.return_value = []

        signals = compute_cycle_signals(
            WorkflowState(),
            _perception_snapshot(),
            _execution_result(),
            _evaluation_result(meaningful_progress=False, grid_changed=True),
            anchor_ref=18,
            anchor_type="entity",
            deepening_cycle_count=0,
            already_retried=False,
            graph_port=graph_port,
        )
        assert signals.domain == CynefinDomain.CONVERGED
        graph_port.fetch_entity_history.assert_not_called()
