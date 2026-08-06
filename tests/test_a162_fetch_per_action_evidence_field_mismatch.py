"""Tests for A162: fetch_per_action_evidence reads the server's real falsified_count/evidence_count fields."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.graph_queries import ArcGraphQueryPort


class _StubBrainClient:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    def call_tool(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._result


def _port(result: dict[str, Any]) -> ArcGraphQueryPort:
    return ArcGraphQueryPort(_StubBrainClient(result), task_id="task-1", session_id="session-1", strict=False)


class TestFetchPerActionEvidenceFieldMismatch:
    def test_real_server_shape_falsified_count_maps_to_contradictions(self):
        port = _port(
            {
                "tested": True,
                "confidence": 0.0,
                "falsified_count": 7,
                "evidence_count": 12,
                "steps_used": 22,
                "causal_power": 0.0,
                "value_status": "unknown",
            }
        )
        evidence = port.fetch_per_action_evidence("ACTION1")
        assert evidence["contradictions"] == 7
        assert evidence["attempts"] == 12

    def test_confidence_still_passes_through_directly(self):
        port = _port(
            {
                "tested": True,
                "confidence": 0.42,
                "falsified_count": 7,
                "evidence_count": 12,
                "steps_used": 22,
                "causal_power": 0.0,
                "value_status": "unknown",
            }
        )
        assert port.fetch_per_action_evidence("ACTION1")["confidence"] == 0.42

    def test_legacy_contradictions_key_takes_priority(self):
        port = _port({"contradictions": 2, "falsified_count": 9})
        assert port.fetch_per_action_evidence("ACTION1")["contradictions"] == 2

    def test_capability_missing_still_all_zero(self):
        port = _port({"status": "capability_missing"})
        evidence = port.fetch_per_action_evidence("ACTION1")
        assert evidence == {"supports": 0, "contradictions": 0, "confidence": 0.0, "attempts": 0}
