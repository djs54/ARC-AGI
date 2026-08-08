"""Tests for A179: `action_signature` (hash of which buttons a game exposes)
is a near-meaningless cross-game similarity key -- confirmed live, two
unrelated mechanic records with identical signatures both read "the game
follows a space archetype." Real transfer needs to key on what a rule
*claims* (action_family + color-invariant magnitude shape), not on which
buttons happen to exist. See backlog/A179.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.goal_resolver import GoalResolver, GoalResolverLimits
from agents.arc4.graph_queries import ArcGraphQueryPort
from agents.arc4.rule_extraction import StructuralFingerprint, compute_fingerprint, magnitude_class
from agents.arc4.types import ExecutionResult, PerceivedEntity, PerceptionSnapshot, PlanCandidate, WorkflowState


class _StubBrainClient:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result if result is not None else {"ok": True}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, payload))
        return self.result


def _execution(action_id: str = "ACTION6") -> ExecutionResult:
    return ExecutionResult(
        action_id=action_id,
        candidate=PlanCandidate(action_id=action_id, goal_id="goal-1"),
        observation={"grid": "new-hash"},
        did_progress=True,
        metadata={"step": 3},
    )


class TestMagnitudeClass:
    def test_single_cell(self):
        assert magnitude_class(1) == "single"
        assert magnitude_class(0) == "single"

    def test_small(self):
        assert magnitude_class(2) == "small"
        assert magnitude_class(4) == "small"

    def test_medium(self):
        assert magnitude_class(5) == "medium"
        assert magnitude_class(19) == "medium"

    def test_large(self):
        assert magnitude_class(20) == "large"
        assert magnitude_class(500) == "large"


class TestFingerprintInvariance:
    def test_same_action_family_and_magnitude_are_color_invariant(self):
        """Same underlying mechanic shape, different colors -> same fingerprint."""
        fp_a = compute_fingerprint("ACTION6", changed_count=3)
        fp_b = compute_fingerprint("ACTION6", changed_count=3)
        assert fp_a == fp_b
        assert fp_a.key() == fp_b.key()

    def test_action_id_suffix_stripped_like_action_family(self):
        """A click-target book_id suffix (e.g. "ACTION6@12,34") must not
        fragment the fingerprint space -- two clicks on different cells that
        both toggle a single cell are the same mechanic shape."""
        fp_a = compute_fingerprint("ACTION6@12,34", changed_count=1)
        fp_b = compute_fingerprint("ACTION6@56,78", changed_count=1)
        assert fp_a == fp_b


class TestFingerprintDiscrimination:
    def test_different_action_families_do_not_collide(self):
        fp_a = compute_fingerprint("ACTION6", changed_count=3)
        fp_b = compute_fingerprint("ACTION7", changed_count=3)
        assert fp_a.key() != fp_b.key()

    def test_different_magnitudes_do_not_collide(self):
        fp_a = compute_fingerprint("ACTION6", changed_count=1)
        fp_b = compute_fingerprint("ACTION6", changed_count=30)
        assert fp_a.key() != fp_b.key()

    def test_key_format(self):
        fp = StructuralFingerprint(action_family="ACTION6", magnitude="small")
        assert fp.key() == "ACTION6:small"


class TestRecordRuleEvidenceIncludesFingerprint:
    def test_payload_includes_fingerprint(self):
        stub = _StubBrainClient()
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        grid_diff = {
            "changed_cells": [{"row": 0, "col": 0, "from": 2, "to": 5}],
            "changed_count": 1,
            "truncated": False,
        }

        port.record_rule_evidence(_execution("ACTION6"), grid_diff)

        _, payload = stub.calls[0]
        assert payload["fingerprint"] == "ACTION6:single"

    def test_fingerprint_reflects_actual_changed_count_not_signature_count(self):
        """changed_count drives the magnitude bucket, independent of how many
        distinct (from, to) color pairs happened to appear in the diff."""
        stub = _StubBrainClient()
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        grid_diff = {
            "changed_cells": [{"row": r, "col": 0, "from": 1, "to": 2} for r in range(10)],
            "changed_count": 10,
            "truncated": False,
        }

        port.record_rule_evidence(_execution("ACTION6"), grid_diff)

        _, payload = stub.calls[0]
        assert payload["fingerprint"] == "ACTION6:medium"


class TestFetchTransferredRules:
    def test_returns_normalized_rules_when_available(self):
        stub = _StubBrainClient(
            result={
                "rules": [
                    {"rule_id": "rule-A", "confidence": 0.6, "source_game_id": "game-A"},
                ]
            }
        )
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)

        rules = port.fetch_transferred_rules("ACTION6:small")

        tool_name, payload = stub.calls[0]
        assert tool_name == "arc_get_transferred_rules"
        assert payload == {"task_id": "task-1", "fingerprint": "ACTION6:small"}
        assert rules == [{"rule_id": "rule-A", "confidence": 0.6, "source_game_id": "game-A"}]

    def test_capability_missing_degrades_to_empty_list(self):
        stub = _StubBrainClient(result={"status": "capability_missing"})
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        assert port.fetch_transferred_rules("ACTION6:small") == []

    def test_malformed_result_degrades_to_empty_list(self):
        stub = _StubBrainClient(result={"rules": "not-a-list"})
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        assert port.fetch_transferred_rules("ACTION6:small") == []


class _TransferGraphPort:
    """A graph port whose entity has real in-game history AND cross-game
    transfer matches -- exercises goal_resolver.py's A179 consumer block."""

    def __init__(self, transferred: list[dict[str, Any]]) -> None:
        self._transferred = transferred
        self.transfer_calls: list[str] = []

    def fetch_goal_evidence(self, perception, goal=None):
        return {}

    def fetch_entity_history(self, entity_ref):
        return {
            "transitions": [{"action_id": "ACTION6", "changed_count": 1, "step": 2}],
            "changed_count_total": 3,
        }

    def fetch_transferred_rules(self, fingerprint_key: str) -> list[dict[str, Any]]:
        self.transfer_calls.append(fingerprint_key)
        return self._transferred


class _InGameOnlyGraphPort:
    """Same in-game history, but no fetch_transferred_rules at all -- the
    pre-A179 shape, must keep working (backward compatibility)."""

    def fetch_goal_evidence(self, perception, goal=None):
        return {}

    def fetch_entity_history(self, entity_ref):
        return {
            "transitions": [{"action_id": "ACTION6", "changed_count": 1, "step": 2}],
            "changed_count_total": 3,
        }


def _perception_with_history_entity() -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"grid": "hash-1"},
        grid_hash="hash-1",
        entities=(PerceivedEntity(kind="point", value="5", attributes={"entity_ref": 1}),),
    )


class TestGoalResolverConsumesTransferredRules:
    def test_transfer_match_boosts_confidence_and_tags_evidence(self):
        perception = _perception_with_history_entity()
        transfer_port = _TransferGraphPort(transferred=[{"rule_id": "rule-A", "confidence": 0.6, "source_game_id": "game-A"}])
        in_game_only_port = _InGameOnlyGraphPort()

        with_transfer = GoalResolver(GoalResolverLimits()).resolve(WorkflowState(), perception, graph_port=transfer_port).payload
        in_game_only = GoalResolver(GoalResolverLimits()).resolve(WorkflowState(), perception, graph_port=in_game_only_port).payload

        assert "entity_history:transfer_match" in with_transfer.selected.evidence
        assert "entity_history:transfer_match" not in in_game_only.selected.evidence
        assert with_transfer.selected.confidence > in_game_only.selected.confidence
        assert transfer_port.transfer_calls == ["ACTION6:single"]

    def test_no_transfer_matches_leaves_confidence_unchanged(self):
        perception = _perception_with_history_entity()
        empty_transfer_port = _TransferGraphPort(transferred=[])
        in_game_only_port = _InGameOnlyGraphPort()

        with_empty_transfer = GoalResolver(GoalResolverLimits()).resolve(WorkflowState(), perception, graph_port=empty_transfer_port).payload
        in_game_only = GoalResolver(GoalResolverLimits()).resolve(WorkflowState(), perception, graph_port=in_game_only_port).payload

        assert "entity_history:transfer_match" not in with_empty_transfer.selected.evidence
        assert with_empty_transfer.selected.confidence == in_game_only.selected.confidence

    def test_missing_fetch_transferred_rules_degrades_cleanly(self):
        """Backward compatibility: a graph port without fetch_transferred_rules
        (the pre-A179 shape) must not raise -- getattr(..., None) short-circuits."""
        perception = _perception_with_history_entity()
        result = GoalResolver(GoalResolverLimits()).resolve(WorkflowState(), perception, graph_port=_InGameOnlyGraphPort()).payload
        assert "entity_history:transfer_match" not in result.selected.evidence

    def test_transfer_confidence_boost_is_smaller_than_in_game_boost(self):
        """Regression guard (A179 acceptance criteria): a transfer is a lead,
        not a fact. Its contribution to confidence must be strictly smaller
        than the in-game entity_history:has_changed boost alone."""
        perception = _perception_with_history_entity()

        class _NoHistoryPort:
            def fetch_goal_evidence(self, perception, goal=None):
                return {}

            def fetch_entity_history(self, entity_ref):
                return {"transitions": [], "changed_count_total": 0}

        baseline = GoalResolver(GoalResolverLimits()).resolve(WorkflowState(), perception, graph_port=_NoHistoryPort()).payload
        in_game_only = GoalResolver(GoalResolverLimits()).resolve(WorkflowState(), perception, graph_port=_InGameOnlyGraphPort()).payload
        with_transfer = GoalResolver(GoalResolverLimits()).resolve(
            WorkflowState(),
            perception,
            graph_port=_TransferGraphPort(transferred=[{"rule_id": "rule-A", "confidence": 1.0, "source_game_id": "game-A"}]),
        ).payload

        in_game_boost = in_game_only.selected.confidence - baseline.selected.confidence
        transfer_boost = with_transfer.selected.confidence - in_game_only.selected.confidence

        assert in_game_boost > 0
        assert 0 < transfer_boost < in_game_boost
