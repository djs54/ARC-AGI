"""Tests for A186: A179 transfers individual rules across games by
structural fingerprint, but never checks whether transferred rules that
share a fingerprint actually agree on more than that, and never fuses them
into a reusable Mechanic record. A179's own review already found the
fingerprint alone can collide on unrelated mechanics ("same buttons exist"
described two unrelated games identically) -- this adds blocking (reuse the
fingerprint) + structure-layer matching (shared precondition features) +
a conservative, deterministic merge policy on top. See backlog/A186.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.goal_resolver import GoalResolver, GoalResolverLimits
from agents.arc4.graph_queries import ArcGraphQueryPort
from agents.arc4.mechanic_fusion import (
    CONFIDENT_MATCH_MIN_SHARED_PRECONDITIONS,
    MechanicFusionResult,
    TransferredRuleRecord,
    block_by_fingerprint,
    fuse_transferred_rules,
    match_within_block,
    merge_confident_candidates,
)
from agents.arc4.rule_extraction import entity_preconditions, shape_class
from agents.arc4.types import ExecutionResult, PerceivedEntity, PerceptionSnapshot, PlanCandidate, WorkflowState


def _rule(
    rule_id: str,
    *,
    confidence: float = 0.5,
    source_game_id: str = "game-A",
    fingerprint: str = "ACTION6:small",
    preconditions: tuple[str, ...] = (),
) -> TransferredRuleRecord:
    return TransferredRuleRecord(
        rule_id=rule_id,
        confidence=confidence,
        source_game_id=source_game_id,
        fingerprint=fingerprint,
        preconditions=preconditions,
    )


SHARED_3 = ("kind:blob", "size_class:small", "shape_class:square")
DISJOINT_3 = ("kind:point", "size_class:large", "shape_class:tall")


class TestShapeClass:
    def test_square(self):
        assert shape_class(4, 4) == "square"
        assert shape_class(5, 4) == "square"

    def test_tall(self):
        assert shape_class(10, 4) == "tall"

    def test_wide(self):
        assert shape_class(2, 10) == "wide"

    def test_degenerate(self):
        assert shape_class(0, 4) == "degenerate"
        assert shape_class(4, 0) == "degenerate"


class TestEntityPreconditions:
    def test_full_feature_set(self):
        features = entity_preconditions("blob", 3, [0, 0, 3, 3])
        assert features == ["kind:blob", "size_class:small", "shape_class:square"]

    def test_no_color_in_features(self):
        """Preconditions must be palette-invariant -- same reasoning as
        A179's fingerprint excluding literal color."""
        features = entity_preconditions("blob", 3, [0, 0, 3, 3])
        assert not any("color" in feature for feature in features)

    def test_missing_optional_fields_degrade_gracefully(self):
        assert entity_preconditions("point", None, None) == ["kind:point"]


class TestBlockByFingerprint:
    def test_matching_fingerprints_share_a_block(self):
        rules = [_rule("r1", fingerprint="ACTION6:small"), _rule("r2", fingerprint="ACTION6:small")]
        blocks = block_by_fingerprint(rules)
        assert list(blocks.keys()) == ["ACTION6:small"]
        assert len(blocks["ACTION6:small"]) == 2

    def test_different_fingerprints_never_share_a_block(self):
        rules = [
            _rule("r1", fingerprint="ACTION6:small"),
            _rule("r2", fingerprint="ACTION7:small"),
            _rule("r3", fingerprint="ACTION6:large"),
        ]
        blocks = block_by_fingerprint(rules)
        assert set(blocks.keys()) == {"ACTION6:small", "ACTION7:small", "ACTION6:large"}
        assert all(len(members) == 1 for members in blocks.values())


class TestMatchWithinBlock:
    def test_confident_match_on_shared_preconditions(self):
        block = [
            _rule("r1", preconditions=SHARED_3),
            _rule("r2", preconditions=SHARED_3),
        ]
        candidates = match_within_block(block)
        assert len(candidates) == 1
        assert candidates[0].confident is True
        assert set(candidates[0].member_rule_ids) == {"r1", "r2"}

    def test_same_fingerprint_disjoint_preconditions_does_not_match(self):
        """Regression guard: sharing a fingerprint is not enough on its own
        -- A179's own review found two unrelated mechanics sharing one."""
        block = [
            _rule("r1", preconditions=SHARED_3),
            _rule("r2", preconditions=DISJOINT_3),
        ]
        candidates = match_within_block(block)
        assert all(not candidate.confident for candidate in candidates)
        assert {candidate.member_rule_ids for candidate in candidates} == {("r1",), ("r2",)}

    def test_below_threshold_shared_features_does_not_match(self):
        below_threshold = SHARED_3[: CONFIDENT_MATCH_MIN_SHARED_PRECONDITIONS - 1]
        block = [
            _rule("r1", preconditions=below_threshold),
            _rule("r2", preconditions=below_threshold),
        ]
        candidates = match_within_block(block)
        assert all(not candidate.confident for candidate in candidates)

    def test_empty_preconditions_never_match(self):
        """The real, current server behavior (fetch_transferred_rules
        doesn't return preconditions yet) must fail closed to no merge, not
        raise or false-match."""
        block = [_rule("r1", preconditions=()), _rule("r2", preconditions=())]
        candidates = match_within_block(block)
        assert all(not candidate.confident for candidate in candidates)

    def test_empty_block_returns_empty(self):
        assert match_within_block([]) == []


class TestMergeConfidentCandidates:
    def test_deterministic_merge_unions_ids_and_provenance(self):
        block = [
            _rule("r1", confidence=0.4, source_game_id="game-A", preconditions=SHARED_3),
            _rule("r2", confidence=0.6, source_game_id="game-B", preconditions=SHARED_3),
        ]
        candidates = match_within_block(block)
        results = merge_confident_candidates(block, candidates)
        assert len(results) == 1
        result = results[0]
        assert set(result.member_rule_ids) == {"r1", "r2"}
        assert set(result.source_game_ids) == {"game-A", "game-B"}
        assert set(result.merged_from) == {"r1", "r2"}

    def test_aggregate_confidence_never_exceeds_strongest_member(self):
        block = [
            _rule("r1", confidence=0.2, preconditions=SHARED_3),
            _rule("r2", confidence=0.9, preconditions=SHARED_3),
        ]
        candidates = match_within_block(block)
        results = merge_confident_candidates(block, candidates)
        assert results[0].confidence < 0.9

    def test_ambiguous_candidates_stay_unmerged(self):
        """Non-confident (single-member) candidates never produce a
        MechanicFusionResult."""
        block = [
            _rule("r1", preconditions=SHARED_3),
            _rule("r2", preconditions=DISJOINT_3),
        ]
        candidates = match_within_block(block)
        results = merge_confident_candidates(block, candidates)
        assert results == []


class TestFuseTransferredRules:
    def test_end_to_end_two_blocks_one_fuses_one_does_not(self):
        rules = [
            _rule("r1", fingerprint="ACTION6:small", preconditions=SHARED_3),
            _rule("r2", fingerprint="ACTION6:small", preconditions=SHARED_3),
            _rule("r3", fingerprint="ACTION7:large", preconditions=SHARED_3),
        ]
        results = fuse_transferred_rules(rules)
        assert len(results) == 1
        assert results[0].fingerprint == "ACTION6:small"
        assert set(results[0].member_rule_ids) == {"r1", "r2"}

    def test_no_rules_produces_no_fusions(self):
        assert fuse_transferred_rules([]) == []


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


class TestRecordRuleEvidenceIncludesPreconditions:
    def test_preconditions_computed_from_attributed_entity(self):
        stub = _StubBrainClient()
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        grid_diff = {
            "changed_cells": [{"row": 0, "col": 0, "from": 2, "to": 5}],
            "changed_count": 1,
            "truncated": False,
        }
        entity = PerceivedEntity(
            kind="blob",
            value="1",
            attributes={"entity_ref": "e1", "bbox": [0, 0, 3, 3], "cell_count": 3},
        )

        port.record_rule_evidence(_execution("ACTION6"), grid_diff, entities=[entity])

        _, payload = stub.calls[0]
        assert payload["preconditions"] == ["kind:blob", "size_class:small", "shape_class:square"]

    def test_no_entities_gives_empty_preconditions(self):
        stub = _StubBrainClient()
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        grid_diff = {
            "changed_cells": [{"row": 0, "col": 0, "from": 2, "to": 5}],
            "changed_count": 1,
            "truncated": False,
        }

        port.record_rule_evidence(_execution("ACTION6"), grid_diff)

        _, payload = stub.calls[0]
        assert payload["preconditions"] == []


class TestFetchTransferredRulesIncludesPreconditions:
    def test_preconditions_parsed_when_present(self):
        stub = _StubBrainClient(
            result={
                "rules": [
                    {"rule_id": "rule-A", "confidence": 0.6, "source_game_id": "game-A", "preconditions": list(SHARED_3)},
                ]
            }
        )
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)

        rules = port.fetch_transferred_rules("ACTION6:small")

        assert rules[0]["preconditions"] == SHARED_3
        assert rules[0]["fingerprint"] == "ACTION6:small"

    def test_missing_preconditions_defaults_to_empty_tuple(self):
        """The real, current server shape -- no preconditions field at all."""
        stub = _StubBrainClient(result={"rules": [{"rule_id": "rule-A", "confidence": 0.6, "source_game_id": "game-A"}]})
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)

        rules = port.fetch_transferred_rules("ACTION6:small")

        assert rules[0]["preconditions"] == ()


class TestRecordMechanicFusion:
    def test_sends_expected_payload(self):
        stub = _StubBrainClient()
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        fusion = MechanicFusionResult(
            fingerprint="ACTION6:small",
            member_rule_ids=("r1", "r2"),
            source_game_ids=("game-A", "game-B"),
            confidence=0.42,
            merged_from=("r1", "r2"),
        )

        port.record_mechanic_fusion(fusion)

        tool_name, payload = stub.calls[0]
        assert tool_name == "record_mechanic"
        assert payload["fingerprint"] == "ACTION6:small"
        assert payload["member_rule_ids"] == ["r1", "r2"]
        assert payload["confidence"] == 0.42


class TestFetchMechanicCandidates:
    def test_capability_missing_degrades_to_empty_list(self):
        stub = _StubBrainClient(result={"status": "capability_missing"})
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        assert port.fetch_mechanic_candidates("ACTION6:small") == []

    def test_malformed_result_degrades_to_empty_list(self):
        stub = _StubBrainClient(result={"mechanics": "not-a-list"})
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        assert port.fetch_mechanic_candidates("ACTION6:small") == []

    def test_returns_normalized_mechanics_when_available(self):
        stub = _StubBrainClient(result={"mechanics": [{"mechanic_id": "m1", "confidence": 0.5, "member_rule_ids": ["r1", "r2"]}]})
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        result = port.fetch_mechanic_candidates("ACTION6:small")
        assert result == [{"mechanic_id": "m1", "confidence": 0.5, "member_rule_ids": ["r1", "r2"]}]


class _MechanicGraphPort:
    """A graph port with real in-game entity history AND >=2 transferred
    rules sharing a fingerprint. `preconditions` on each is controlled per
    test to exercise both the confident-match and no-match paths."""

    def __init__(self, transferred: list[dict[str, Any]]) -> None:
        self._transferred = transferred
        self.recorded_fusions: list[Any] = []

    def fetch_goal_evidence(self, perception, goal=None):
        return {}

    def fetch_entity_history(self, entity_ref):
        return {
            "transitions": [{"action_id": "ACTION6", "changed_count": 1, "step": 2}],
            "changed_count_total": 3,
        }

    def fetch_transferred_rules(self, fingerprint_key: str) -> list[dict[str, Any]]:
        return self._transferred

    def record_mechanic_fusion(self, fusion) -> dict[str, Any]:
        self.recorded_fusions.append(fusion)
        return {"status": "ok"}


def _perception_with_history_entity() -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"grid": "hash-1"},
        grid_hash="hash-1",
        entities=(PerceivedEntity(kind="point", value="5", attributes={"entity_ref": 1}),),
    )


class TestGoalResolverConsumesMechanicFusion:
    def test_confident_fusion_boosts_confidence_and_tags_evidence(self):
        perception = _perception_with_history_entity()
        port = _MechanicGraphPort(
            transferred=[
                {"rule_id": "r1", "confidence": 0.6, "source_game_id": "game-A", "fingerprint": "ACTION6:single", "preconditions": list(SHARED_3)},
                {"rule_id": "r2", "confidence": 0.5, "source_game_id": "game-B", "fingerprint": "ACTION6:single", "preconditions": list(SHARED_3)},
            ]
        )

        result = GoalResolver(GoalResolverLimits()).resolve(WorkflowState(), perception, graph_port=port).payload

        assert "entity_history:mechanic_fusion" in result.selected.evidence
        assert len(port.recorded_fusions) == 1
        assert set(port.recorded_fusions[0].member_rule_ids) == {"r1", "r2"}

    def test_no_shared_preconditions_no_fusion_boost(self):
        """Today's real server shape: fetch_transferred_rules returns no
        preconditions at all -- must degrade to no fusion, not raise."""
        perception = _perception_with_history_entity()
        port = _MechanicGraphPort(
            transferred=[
                {"rule_id": "r1", "confidence": 0.6, "source_game_id": "game-A"},
                {"rule_id": "r2", "confidence": 0.5, "source_game_id": "game-B"},
            ]
        )

        result = GoalResolver(GoalResolverLimits()).resolve(WorkflowState(), perception, graph_port=port).payload

        assert "entity_history:mechanic_fusion" not in result.selected.evidence
        assert port.recorded_fusions == []

    def test_single_transferred_rule_no_fusion_possible(self):
        perception = _perception_with_history_entity()
        port = _MechanicGraphPort(
            transferred=[{"rule_id": "r1", "confidence": 0.6, "source_game_id": "game-A", "preconditions": list(SHARED_3)}]
        )

        result = GoalResolver(GoalResolverLimits()).resolve(WorkflowState(), perception, graph_port=port).payload

        assert "entity_history:mechanic_fusion" not in result.selected.evidence

    def test_missing_record_mechanic_fusion_degrades_cleanly(self):
        """A graph port with fetch_transferred_rules but no
        record_mechanic_fusion (the pre-A186 shape) must still compute and
        apply the boost -- persistence is best-effort, not required."""
        perception = _perception_with_history_entity()

        class _NoWritePort:
            def fetch_goal_evidence(self, perception, goal=None):
                return {}

            def fetch_entity_history(self, entity_ref):
                return {"transitions": [{"action_id": "ACTION6", "changed_count": 1, "step": 2}], "changed_count_total": 3}

            def fetch_transferred_rules(self, fingerprint_key: str) -> list[dict[str, Any]]:
                return [
                    {"rule_id": "r1", "confidence": 0.6, "source_game_id": "game-A", "preconditions": list(SHARED_3)},
                    {"rule_id": "r2", "confidence": 0.5, "source_game_id": "game-B", "preconditions": list(SHARED_3)},
                ]

        result = GoalResolver(GoalResolverLimits()).resolve(WorkflowState(), perception, graph_port=_NoWritePort()).payload
        assert "entity_history:mechanic_fusion" in result.selected.evidence

    def test_record_mechanic_fusion_exception_does_not_block_boost(self):
        perception = _perception_with_history_entity()

        class _RaisingWritePort(_MechanicGraphPort):
            def record_mechanic_fusion(self, fusion):
                raise RuntimeError("server unavailable")

        port = _RaisingWritePort(
            transferred=[
                {"rule_id": "r1", "confidence": 0.6, "source_game_id": "game-A", "preconditions": list(SHARED_3)},
                {"rule_id": "r2", "confidence": 0.5, "source_game_id": "game-B", "preconditions": list(SHARED_3)},
            ]
        )

        result = GoalResolver(GoalResolverLimits()).resolve(WorkflowState(), perception, graph_port=port).payload
        assert "entity_history:mechanic_fusion" in result.selected.evidence

    def test_mechanic_boost_strictly_smaller_than_transfer_boost(self):
        """Pin the acceptance-criteria ordering:
        0 < mechanic_boost < transfer_boost < in_game_boost."""
        perception = _perception_with_history_entity()

        class _NoHistoryPort:
            def fetch_goal_evidence(self, perception, goal=None):
                return {}

            def fetch_entity_history(self, entity_ref):
                return {"transitions": [], "changed_count_total": 0}

        class _InGameOnlyPort:
            def fetch_goal_evidence(self, perception, goal=None):
                return {}

            def fetch_entity_history(self, entity_ref):
                return {"transitions": [{"action_id": "ACTION6", "changed_count": 1, "step": 2}], "changed_count_total": 3}

        class _SingleTransferPort(_InGameOnlyPort):
            def fetch_transferred_rules(self, fingerprint_key: str) -> list[dict[str, Any]]:
                return [{"rule_id": "r1", "confidence": 1.0, "source_game_id": "game-A"}]

        class _FusedTransferPort(_InGameOnlyPort):
            def fetch_transferred_rules(self, fingerprint_key: str) -> list[dict[str, Any]]:
                return [
                    {"rule_id": "r1", "confidence": 1.0, "source_game_id": "game-A", "preconditions": list(SHARED_3)},
                    {"rule_id": "r2", "confidence": 1.0, "source_game_id": "game-B", "preconditions": list(SHARED_3)},
                ]

        baseline = GoalResolver(GoalResolverLimits()).resolve(WorkflowState(), perception, graph_port=_NoHistoryPort()).payload
        in_game_only = GoalResolver(GoalResolverLimits()).resolve(WorkflowState(), perception, graph_port=_InGameOnlyPort()).payload
        with_transfer = GoalResolver(GoalResolverLimits()).resolve(WorkflowState(), perception, graph_port=_SingleTransferPort()).payload
        with_fusion = GoalResolver(GoalResolverLimits()).resolve(WorkflowState(), perception, graph_port=_FusedTransferPort()).payload

        in_game_boost = in_game_only.selected.confidence - baseline.selected.confidence
        transfer_boost = with_transfer.selected.confidence - in_game_only.selected.confidence
        mechanic_boost = with_fusion.selected.confidence - with_transfer.selected.confidence

        assert in_game_boost > 0
        assert 0 < transfer_boost < in_game_boost
        assert 0 < mechanic_boost < transfer_boost


class TestA164ScopingRegression:
    """Blocking/matching must only ever operate on rules that arrived
    through fetch_transferred_rules's already-scoped cross-game path --
    mechanic_fusion.py itself has no graph_port/task_id and cannot read raw
    evidence directly, which is the structural guarantee this test pins."""

    def test_mechanic_fusion_module_has_no_io_dependencies(self):
        import agents.arc4.mechanic_fusion as module

        assert not hasattr(module, "ArcGraphQueryPort")
        assert not hasattr(module, "brain_client")

    def test_fuse_transferred_rules_is_pure_function_of_its_argument(self):
        """Same input always produces the same output -- no hidden state,
        no network call, so it cannot pool evidence from anywhere other than
        the rules explicitly passed in."""
        rules = [
            _rule("r1", preconditions=SHARED_3),
            _rule("r2", preconditions=SHARED_3),
        ]
        first = fuse_transferred_rules(rules)
        second = fuse_transferred_rules(rules)
        assert first == second


class TestBackwardCompatibility:
    def test_old_two_arg_record_rule_evidence_stub_still_works(self):
        """evaluator.py's _accepts_entities_param must detect a pre-A186
        graph port (record_rule_evidence(execution, grid_diff), no entities
        parameter) and call it without the third argument."""
        from agents.arc4.evaluator import Evaluator

        calls: list[tuple[Any, ...]] = []

        class _OldPort:
            def record_rule_evidence(self, execution, grid_diff):
                calls.append((execution, grid_diff))
                return {"status": "ok"}

        record = _OldPort().record_rule_evidence
        assert Evaluator._accepts_entities_param(record) is False
