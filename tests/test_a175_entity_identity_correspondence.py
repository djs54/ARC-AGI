"""Tests for A175: grid entities had no real or temporally-stable identity.
entity_id = f"{task}_e{color_id}_{region_index}" always resolved to _e0_0
because the client never sent either field, collapsing every entity in a
task into one degenerate GridEntity node and leaving MOVED_BY permanently
unwritten (which is why arc_get_causal_path, consumed by A163, always
returned path_exists=False). See backlog/A175.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.graph_queries import ArcGraphQueryPort
from agents.arc4.perceive import PerceiveAgent
from agents.arc4.types import PerceptionSnapshot, WorkflowState


class TestFrameToFrameCorrespondence:
    def test_same_entity_matched_across_steps(self):
        agent = PerceiveAgent()
        state = WorkflowState()

        first = agent.perceive(state, {"grid": [[0, 0, 0], [0, 5, 0], [0, 0, 0]]})
        second = agent.perceive(state, {"grid": [[0, 0, 0], [0, 0, 5], [0, 0, 0]]})

        first_ref = first.payload.entities[0].attributes["entity_ref"]
        second_ref = second.payload.entities[0].attributes["entity_ref"]
        assert first_ref == second_ref

    def test_disappeared_and_new_entity_not_confused(self):
        agent = PerceiveAgent()
        state = WorkflowState()

        first = agent.perceive(state, {"grid": [[5, 0, 0, 0, 0, 0, 0, 0, 0, 0]]})
        second = agent.perceive(state, {"grid": [[0, 0, 0, 0, 0, 0, 0, 0, 0, 5]]})

        first_ref = first.payload.entities[0].attributes["entity_ref"]
        second_ref = second.payload.entities[0].attributes["entity_ref"]
        assert first_ref != second_ref

    def test_fresh_id_minted_on_first_appearance(self):
        agent = PerceiveAgent()
        state = WorkflowState()
        result = agent.perceive(state, {"grid": [[0, 5], [0, 0]]})
        assert result.payload.entities[0].attributes["entity_ref"] == 0
        assert state.next_entity_ref == 1

    def test_multiple_same_color_entities_do_not_cross_match(self):
        """Known-limitation case documented explicitly: two same-color entities
        both present and close together in consecutive steps. Simple greedy
        nearest-centroid matching (A175's Step 0 choice) may not always assign
        correspondence "correctly" in an object-permanence sense here -- this
        test asserts deterministic, non-crashing, non-colliding behavior
        (each new entity gets exactly one distinct ref), not perfect matching.
        """
        agent = PerceiveAgent()
        state = WorkflowState()

        agent.perceive(state, {"grid": [[5, 0, 0, 5]]})
        result = agent.perceive(state, {"grid": [[0, 5, 5, 0]]})

        refs = [e.attributes["entity_ref"] for e in result.payload.entities]
        assert len(refs) == len(set(refs)), "each entity in the same step must get a distinct ref"


class _StubBrainClient:
    def __init__(self) -> None:
        self.last_payload: dict[str, Any] | None = None

    def call_tool(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.last_payload = payload
        return {"ok": True}


class TestSerializeEntitySendsCorrectedFields:
    def test_serialize_entity_sends_color_id_and_correspondence_id(self):
        agent = PerceiveAgent()
        state = WorkflowState()
        result = agent.perceive(state, {"grid": [[0, 7], [0, 0]]})
        perception = result.payload

        stub = _StubBrainClient()
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        port.ingest_perception(perception)

        assert stub.last_payload is not None
        sent_entity = stub.last_payload["entities"][0]
        assert sent_entity["color_id"] == 7
        assert sent_entity["region_index"] == 0
        assert sent_entity["centroid_row"] is not None
        assert sent_entity["centroid_col"] is not None
        assert sent_entity["pixel_count"] == 1
