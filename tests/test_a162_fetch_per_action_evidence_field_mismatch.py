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

    def test_steps_used_fallback_populates_attempts_when_evidence_count_absent(self):
        """A165: evidence_count is never written server-side, but observation_count is,
        surfaced under the key steps_used -- attempts should fall back to it."""
        port = _port({"falsified_count": 2, "steps_used": 5})
        assert port.fetch_per_action_evidence("ACTION1")["attempts"] == 5

    def test_evidence_count_still_takes_priority_over_steps_used(self):
        """Regression guard: if hippocampy ever wires a real evidence_count, it must
        keep taking priority over the steps_used fallback."""
        port = _port({"evidence_count": 3, "steps_used": 5})
        assert port.fetch_per_action_evidence("ACTION1")["attempts"] == 3
