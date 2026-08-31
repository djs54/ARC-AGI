"""Tests for A221 Finding 2: disappearance detection (A219) gets promoted to
a real graph write -- the other five entity_effects classifications
(TRANSLATION/GROWTH/SHRINK/APPEARANCE/UNCHANGED) deliberately stay local
process state, per the settled design (see backlog/A221.md Finding 2 and
backlog/A219.md's "Staying local" note). Disappearance is the one genuinely
new causal fact (A175's correspondence tracking had zero disappearance
detection before A219) worth its own graph write; the rest lack a concrete
consumer to design a schema against yet.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.graph_queries import ArcGraphQueryPort
from agents.arc4.perceive import PerceiveAgent
from agents.arc4.types import PerceivedEntity, PerceptionSnapshot, WorkflowState


class _StubBrainClient:
    def __init__(self) -> None:
        self.last_payload: dict[str, Any] | None = None

    def call_tool(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.last_payload = payload
        return {"ok": True}


class TestPerceiveExposesDisappearedEntitiesAsPlainDicts:
    def test_metadata_carries_plain_dict_disappeared_entities(self):
        """metadata["disappeared_entities"] must be JSON-safe plain dicts
        (PerceivedEntity.to_dict() shape), matching how `entities` itself is
        handled in PerceptionSnapshot.to_dict() -- not raw PerceivedEntity
        dataclass instances, which would break metadata's direct pass-through
        in to_dict()/telemetry export."""
        agent = PerceiveAgent()
        state = WorkflowState()
        agent.perceive(state, {"grid": [[5, 0, 0, 0, 0, 0, 0, 0, 0, 0]]})
        result = agent.perceive(state, {"grid": [[0, 0, 0, 0, 0, 0, 0, 0, 0, 0]]})

        disappeared = result.payload.metadata["disappeared_entities"]
        assert len(disappeared) == 1
        assert isinstance(disappeared[0], dict)
        assert disappeared[0]["kind"] == "point"
        assert disappeared[0]["value"] == "5"
        assert disappeared[0]["attributes"]["entity_ref"] == 0

    def test_no_disappearance_gives_empty_list_not_missing_key(self):
        agent = PerceiveAgent()
        state = WorkflowState()
        agent.perceive(state, {"grid": [[5, 0], [0, 0]]})
        result = agent.perceive(state, {"grid": [[5, 0], [0, 0]]})

        assert result.payload.metadata["disappeared_entities"] == []


class TestIngestPerceptionSendsDisappearedEntities:
    def test_disappeared_entities_serialized_with_full_fidelity(self):
        """Reuses the exact same _serialize_entity() output shape as regular
        entities (color_id/region_index/centroid_row/centroid_col/pixel_count),
        not the sparse entity_ref/kind/value telemetry currently has -- the
        graph gets the same fidelity for a vanished entity as a visible one."""
        snapshot = PerceptionSnapshot(
            observation={},
            grid_hash="h1",
            metadata={
                "disappeared_entities": [
                    PerceivedEntity(
                        kind="point",
                        value="5",
                        attributes={"entity_ref": 3, "color": 5, "centroid": (1.0, 2.0), "cell_count": 1},
                    ).to_dict()
                ],
            },
        )
        stub = _StubBrainClient()
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        port.ingest_perception(snapshot)

        assert stub.last_payload is not None
        sent = stub.last_payload["disappeared_entities"]
        assert len(sent) == 1
        assert sent[0]["color_id"] == 5
        assert sent[0]["region_index"] == 3
        assert sent[0]["centroid_row"] == 1.0
        assert sent[0]["centroid_col"] == 2.0
        assert sent[0]["pixel_count"] == 1

    def test_no_disappeared_entities_sends_empty_list(self):
        """Regression: payload shape stays consistent (empty list, not a
        missing key) when metadata has no disappeared_entities at all --
        e.g. an older/synthetic PerceptionSnapshot that predates this field."""
        snapshot = PerceptionSnapshot(observation={}, grid_hash="h1")
        stub = _StubBrainClient()
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        port.ingest_perception(snapshot)

        assert stub.last_payload["disappeared_entities"] == []

    def test_ingest_perception_still_works_without_graph_query_port_write_call(self):
        """Sanity: this new field doesn't change ingest_perception's existing
        degrade-gracefully contract -- still a plain dict return, no new
        exception paths introduced for the normal (no disappearance) case."""
        agent = PerceiveAgent()
        state = WorkflowState()
        result = agent.perceive(state, {"grid": [[5, 0], [0, 0]]})

        stub = _StubBrainClient()
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        write_result = port.ingest_perception(result.payload)
        assert write_result["status"] == "ok"
