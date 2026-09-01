"""Tests for A233: fetch_goal_evidence reads the server's real `goals` key.

Confirmed live (see backlog/A233.md's Outcome) against the real hippocampy
server: arc_queries.py::arc_get_goal_evidence (sibling hippocampy repo)
returns {"goals": [{"id": <goal_id>, "confidence": ..., ...}, ...]} -- one
VictoryCondition record per real, client-derived goal_id (e.g. "line-4",
"block-red"). ArcGraphQueryPort._extract_sequence's recognized container
keys never included "goals", so the whole per-goal list was silently
discarded and the entire {"goals": [...], "source": "fresh"} mapping
collapsed into a single synthetic, contentless "goal_evidence" record
(confidence 0.0, no real goal_id) -- every individual goal's own graph
confidence was structurally unreachable by _merge_single_record /
_apply_grounding_gate's goal_id-matching, no matter how much real per-goal
data the server actually held. This is the same "field mismatch" pattern as
A160/A161/A162 -- same fix shape (recognize the server's real key), same
test style (assert on the real server response shape, not a guess).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.graph_queries import ArcGraphQueryPort
from agents.arc4.types import PerceptionSnapshot


class _RoutingBrainClient:
    """Routes each MCP tool call to a per-tool canned response, mirroring
    the real server's fetch_goal_evidence pipeline (goal_evidence +
    game_context + mechanic_priors sub-calls)."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses

    def call_tool(self, tool_name: str, payload: dict[str, Any]) -> Any:
        return self._responses.get(tool_name, {"status": "capability_missing"})


def _perception() -> PerceptionSnapshot:
    return PerceptionSnapshot(observation={}, grid_hash="grid-1", grid_shape=(3, 3), entities=())


class TestFetchGoalEvidenceGoalsKeyMismatch:
    def test_real_server_goals_key_is_unwrapped_into_per_goal_records(self):
        port = ArcGraphQueryPort(
            _RoutingBrainClient(
                {
                    "arc_get_goal_evidence": {
                        "goals": [
                            {"id": "line-4", "type": None, "confidence": 0.0, "supports": 0, "contradicts": 2},
                            {"id": "block-red", "type": None, "confidence": 0.82, "supports": 3, "contradicts": 0},
                        ],
                        "source": "fresh",
                    },
                    "arc_get_game_context": {"status": "capability_missing"},
                    "arc_get_mechanic_priors": {"status": "capability_missing"},
                }
            ),
            task_id="task-1",
            session_id="session-1",
            strict=False,
        )

        records = port.fetch_goal_evidence(_perception(), None)

        goal_ids = {record["goal_id"] for record in records}
        assert "line-4" in goal_ids
        assert "block-red" in goal_ids
        # Pre-fix, everything collapsed into one synthetic "goal_evidence"
        # record and neither real goal_id was ever reachable.
        assert "goal_evidence" not in goal_ids

        block_red = next(r for r in records if r["goal_id"] == "block-red")
        assert block_red["confidence"] == 0.82

        line_4 = next(r for r in records if r["goal_id"] == "line-4")
        assert line_4["confidence"] == 0.0

    def test_empty_goals_list_produces_no_real_goal_id_record(self):
        """An empty "goals" list falls back to _extract_sequence's existing
        empty-Sequence behavior (shared by every other recognized key, not
        something this card's fix changes): the whole mapping becomes one
        synthetic fallback record. The fix under test only matters once
        "goals" is non-empty -- this just locks in that the empty case
        doesn't crash or fabricate a fake goal_id."""
        port = ArcGraphQueryPort(
            _RoutingBrainClient(
                {
                    "arc_get_goal_evidence": {"goals": [], "source": "fresh"},
                    "arc_get_game_context": {"status": "capability_missing"},
                    "arc_get_mechanic_priors": {"status": "capability_missing"},
                }
            ),
            task_id="task-1",
            session_id="session-1",
            strict=False,
        )

        records = port.fetch_goal_evidence(_perception(), None)

        assert len(records) == 1
        assert records[0]["goal_id"] == "goal_evidence"
        assert records[0]["confidence"] == 0.0
