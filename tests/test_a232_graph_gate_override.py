"""Tests for A232 (mid-implementation scope addition, found via the
arc-graph-engineering-review the operator ran on the original plan):
`plan_vetter.py::vet`'s graph gate (`check_action_gate` -> server-side
`arc_check_action_gate`, `falsification_count >= 3` veto) reads
`ActionFact.falsified_count` -- the same counter A232's other fix (removing
`record_reward_prediction_error`) stops corrupting *going forward*, but
already-accumulated bad state for in-flight tasks isn't retroactively fixed
by that alone. Unlike the hippocampy-side gate threshold/schema, defending
against a stale/corrupted denial in `plan_vetter.py` is entirely within this
repo's scope.

This adds a cross-check: before letting a `check_action_gate` denial stand,
ask `fetch_rules_for_action` (the real, A177 Rule-graph signal
`plan_generator.py` already trusts elsewhere) for live, unfalsified,
positive-confidence evidence for the same action. If it exists, the denial
is overridden rather than trusted blindly. See backlog/A232.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.plan_vetter import PlanVetter
from agents.arc4.types import PerceptionSnapshot, PlanCandidate, PlanningResult, WorkflowState


class _StubGraphPort:
    """Minimal graph_port double exposing only what PlanVetter touches:
    check_action_gate and (optionally) fetch_rules_for_action."""

    def __init__(self, gate_result: dict[str, Any], rules: list[dict[str, Any]] | None = None, *, has_fetch_rules: bool = True):
        self._gate_result = gate_result
        self._rules = rules or []
        self._has_fetch_rules = has_fetch_rules

    def check_action_gate(self, action_id: str) -> dict[str, Any]:
        return self._gate_result


class _StubGraphPortWithRules(_StubGraphPort):
    def fetch_rules_for_action(self, action_id: str) -> list[dict[str, Any]]:
        return self._rules


def _state(**overrides) -> WorkflowState:
    defaults = dict(
        step_index=0,
        action_attempt_counts={"ACTION1": 3},
        action_falsification_counts={},
        consecutive_no_progress_count=0,
    )
    defaults.update(overrides)
    return WorkflowState(**defaults)


def _perception() -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"grid": "hash-1", "available_actions": ["ACTION1", "ACTION2"]},
        grid_hash="hash-1",
    )


def _plan() -> PlanningResult:
    return PlanningResult(
        candidate=PlanCandidate(action_id="ACTION1", goal_id="goal-1", score=0.5, book_id="ACTION1"),
        alternatives=(PlanCandidate(action_id="ACTION2", goal_id="goal-1", score=0.4, book_id="ACTION2"),),
    )


class TestGraphGateOverriddenByLiveRuleEvidence:
    def test_denial_is_overridden_when_real_unfalsified_rule_evidence_exists(self):
        """The exact scenario this card documented live: check_action_gate
        denies (stale/corrupted falsification_count) while fetch_rules_for_action
        shows a real, unfalsified, positive-confidence rule for the same
        action. The veto must not stand."""
        port = _StubGraphPortWithRules(
            gate_result={"go": False, "reason": "falsified 3 times", "allowed": False},
            rules=[{"rule_id": "r1", "confidence": 0.6, "falsified": False}],
        )
        vetter = PlanVetter(graph_port=port)

        result = vetter.vet(_state(), _perception(), None, _plan())

        assert result.payload.approved is True
        assert result.payload.metadata.get("graph_gate_overridden") is True

    def test_denial_still_vetoes_when_no_live_rule_evidence(self):
        """Regression: a denial with nothing to contradict it must still
        veto, unchanged from today's behavior."""
        port = _StubGraphPortWithRules(
            gate_result={"go": False, "reason": "falsified 3 times", "allowed": False},
            rules=[],
        )
        vetter = PlanVetter(graph_port=port)

        result = vetter.vet(_state(), _perception(), None, _plan())

        assert result.payload.approved is False
        assert result.payload.metadata["veto_type"] == "graph_evidence"

    def test_denial_still_vetoes_when_only_falsified_rules_exist(self):
        """A falsified rule is not live evidence -- must not override the veto."""
        port = _StubGraphPortWithRules(
            gate_result={"go": False, "reason": "falsified 3 times", "allowed": False},
            rules=[{"rule_id": "r1", "confidence": 0.9, "falsified": True}],
        )
        vetter = PlanVetter(graph_port=port)

        result = vetter.vet(_state(), _perception(), None, _plan())

        assert result.payload.approved is False
        assert result.payload.metadata["veto_type"] == "graph_evidence"

    def test_denial_still_vetoes_when_graph_port_lacks_fetch_rules_for_action(self):
        """fetch_rules_for_action isn't part of GraphQueryPort's Protocol --
        a port without it must degrade to the original (no-override) behavior,
        never raise."""
        port = _StubGraphPort(gate_result={"go": False, "reason": "falsified 3 times", "allowed": False})
        vetter = PlanVetter(graph_port=port)

        result = vetter.vet(_state(), _perception(), None, _plan())

        assert result.payload.approved is False
        assert result.payload.metadata["veto_type"] == "graph_evidence"

    def test_allowed_gate_is_unaffected_by_override_logic(self):
        """When the gate already allows the action, the override check is
        irrelevant -- approval proceeds exactly as before, and
        graph_gate_overridden is False since nothing was overridden."""
        port = _StubGraphPortWithRules(
            gate_result={"go": True, "reason": "approved", "allowed": True},
            rules=[{"rule_id": "r1", "confidence": 0.6, "falsified": False}],
        )
        vetter = PlanVetter(graph_port=port)

        result = vetter.vet(_state(), _perception(), None, _plan())

        assert result.payload.approved is True
        assert result.payload.metadata.get("graph_gate_overridden") is False
