"""Tests for A229: `_attribute_entity`'s bbox-overlap heuristic picked
whichever entity's bbox contained the most changed cells, unnormalized by
bbox size and without checking whether the click that caused the step
landed inside that entity's bbox at all. A228's live audit
(`ar25-0c556536`, 2026-08-31) found a background-sized entity absorbing
credit for 3/3 real transitions in a 30-step episode, every time the click
coordinate fell outside that entity's own bbox. A229's own Track A live
smoke reproduced the same pattern generally (7/21 real transitions
misattributed to a bbox the click fell outside of).

Fix (Option 1 from backlog/plans/A-229-attribute-entity-bbox-overlap-bug.md):
require the click coordinate to land inside a candidate entity's bbox
before it is even eligible for the "most changed cells" tiebreak. When no
entity's bbox contains the click, fall back to A218's `_targeted_entity_ref`
(the click's own known target) instead of leaving entity_ref unattributed.
When the click coordinate isn't known at all (non-click actions), behavior
is unchanged from pre-A229: plain unfiltered bbox-overlap.

See backlog/A229.md and backlog/A228.md for the full investigation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.graph_queries import ArcGraphQueryPort
from agents.arc4.types import ExecutionResult, PerceivedEntity, PlanCandidate


class _StubBrainClient:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result if result is not None else {"ok": True}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, payload))
        return self.result


def _port(stub: _StubBrainClient | None = None) -> tuple[ArcGraphQueryPort, _StubBrainClient]:
    stub = stub or _StubBrainClient()
    return ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False), stub


def _click_execution(x: int, y: int, *, entity_ref: Any = None, action_id: str = "ACTION6") -> ExecutionResult:
    metadata: dict[str, Any] = {}
    if entity_ref is not None:
        metadata["entity_ref"] = entity_ref
    candidate = PlanCandidate(action_id=action_id, goal_id="g1", payload={"x": x, "y": y}, metadata=metadata)
    return ExecutionResult(action_id=f"{action_id}@{x},{y}", candidate=candidate, observation={})


class TestClickInsideBboxGating:
    def test_click_outside_large_entity_attributes_to_smaller_entity_click_is_in(self):
        """The exact shape of A228/A229's live failure: a large 'background'
        entity's bbox contains most of the changed cells, but the click
        landed outside its bbox and inside a smaller, second entity's bbox
        instead. Attribution must follow the click, not raw cell count."""
        large_background = PerceivedEntity(kind="blob", value="1", attributes={"bbox": (20, 20, 60, 60), "entity_ref": 0})
        small_target = PerceivedEntity(kind="point", value="5", attributes={"bbox": (0, 0, 5, 5), "entity_ref": 42})

        changed_cells = [
            {"row": 25, "col": 25, "from": 0, "to": 1},
            {"row": 26, "col": 26, "from": 0, "to": 1},
            {"row": 27, "col": 27, "from": 0, "to": 1},
            {"row": 28, "col": 28, "from": 0, "to": 1},
            {"row": 29, "col": 29, "from": 0, "to": 1},
            {"row": 2, "col": 2, "from": 0, "to": 5},
        ]
        grid_diff = {"changed_cells": changed_cells, "changed_count": len(changed_cells)}
        # Click at (x=2, y=2) -> row=2, col=2: inside small_target's bbox,
        # outside large_background's bbox (which contains 5/6 changed cells).
        execution = _click_execution(x=2, y=2)

        port, stub = _port()
        port.record_transition(execution, grid_diff, entities=[large_background, small_target])

        payload = stub.calls[0][1]
        assert payload["entity_ref"] == 42

    def test_click_outside_every_entity_bbox_falls_back_to_targeted_entity_ref(self):
        """When the click lands inside no entity's bbox at all, fall back to
        A218's _targeted_entity_ref (the click's own known target from
        plan_generator.py's click-target metadata) rather than leaving
        entity_ref unattributed or picking a spatially-unrelated entity."""
        large_background = PerceivedEntity(kind="blob", value="1", attributes={"bbox": (20, 20, 60, 60), "entity_ref": 0})

        changed_cells = [{"row": 25, "col": 25, "from": 0, "to": 1}]
        grid_diff = {"changed_cells": changed_cells, "changed_count": 1}
        # Click at (x=1, y=1) -> row=1, col=1: outside the only entity's bbox.
        execution = _click_execution(x=1, y=1, entity_ref=99)

        port, stub = _port()
        port.record_transition(execution, grid_diff, entities=[large_background])

        payload = stub.calls[0][1]
        assert payload["entity_ref"] == 99

    def test_biggest_overlap_still_wins_among_entities_the_click_is_inside(self):
        """Regression: when the click DOES land inside the attributed
        entity's bbox, the original 'most changed cells wins' tiebreak among
        eligible entities is unchanged -- A229 only narrows the candidate
        pool to bboxes containing the click, it doesn't change how ties
        within that pool are broken."""
        entity_a = PerceivedEntity(kind="blob", value="1", attributes={"bbox": (0, 0, 10, 10), "entity_ref": 1})
        entity_b = PerceivedEntity(kind="blob", value="2", attributes={"bbox": (0, 0, 3, 3), "entity_ref": 2})

        # Both bboxes contain the click at (x=1, y=1) -> row=1, col=1.
        # entity_a picks up more changed cells than entity_b.
        changed_cells = [
            {"row": 1, "col": 1, "from": 0, "to": 1},
            {"row": 5, "col": 5, "from": 0, "to": 1},
            {"row": 6, "col": 6, "from": 0, "to": 1},
        ]
        grid_diff = {"changed_cells": changed_cells, "changed_count": len(changed_cells)}
        execution = _click_execution(x=1, y=1)

        port, stub = _port()
        port.record_transition(execution, grid_diff, entities=[entity_a, entity_b])

        payload = stub.calls[0][1]
        assert payload["entity_ref"] == 1

    def test_no_click_coordinate_preserves_pre_a229_unfiltered_behavior(self):
        """Non-click actions (or any execution with no x/y payload) carry no
        click coordinate at all -- A229's gating must not apply, and
        attribution falls back to plain bbox-overlap exactly as before."""
        entity = PerceivedEntity(kind="blob", value="1", attributes={"bbox": (0, 0, 2, 2), "entity_ref": 7})
        changed_cells = [{"row": 1, "col": 1, "from": 0, "to": 5}]
        grid_diff = {"changed_cells": changed_cells, "changed_count": 1}
        candidate = PlanCandidate(action_id="ACTION1", goal_id="g1")
        execution = ExecutionResult(action_id="ACTION1", candidate=candidate, observation={})

        port, stub = _port()
        port.record_transition(execution, grid_diff, entities=[entity])

        payload = stub.calls[0][1]
        assert payload["entity_ref"] == 7

    def test_record_rule_evidence_also_gated_by_click_inside_bbox(self):
        """record_rule_evidence shares _attribute_entity -- the same
        click-inside-bbox gating must apply there too, not just
        record_transition."""
        large_background = PerceivedEntity(kind="blob", value="1", attributes={"bbox": (20, 20, 60, 60), "entity_ref": 0})
        small_target = PerceivedEntity(kind="point", value="5", attributes={"bbox": (0, 0, 5, 5), "entity_ref": 42})

        changed_cells = [
            {"row": 25, "col": 25, "from": 0, "to": 1},
            {"row": 26, "col": 26, "from": 0, "to": 1},
            {"row": 2, "col": 2, "from": 0, "to": 5},
        ]
        grid_diff = {"changed_cells": changed_cells, "changed_count": len(changed_cells)}
        execution = _click_execution(x=2, y=2)

        port, stub = _port()
        port.record_rule_evidence(execution, grid_diff, entities=[large_background, small_target])

        payload = stub.calls[0][1]
        assert payload["entity_ref"] == 42


class TestNoOpFallbackUnaffected:
    def test_no_op_path_still_uses_targeted_entity_ref_directly(self):
        """A218 regression: the no-op path (empty changed_cells) never had
        bbox-overlap evidence to gate in the first place -- it must still go
        straight to _targeted_entity_ref, untouched by A229's changes to the
        real-change path."""
        execution = _click_execution(x=5, y=5, entity_ref=18)

        port, stub = _port()
        port.record_transition(execution, {"changed_cells": []})

        payload = stub.calls[0][1]
        assert payload["entity_ref"] == 18
        assert payload["changed_count"] == 0
