"""Tests for A161: fetch_untested_actions reads the server's real `untested` key."""

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


class TestFetchUntestedActionsKeyMismatch:
    def test_real_server_shape_untested_key_recognized(self):
        port = _port({"untested": ["ACTION5", "ACTION6"], "tested": ["ACTION1", "ACTION2"]})
        assert port.fetch_untested_actions() == ["ACTION5", "ACTION6"]

    def test_empty_untested_list(self):
        port = _port({"untested": [], "tested": ["ACTION1"]})
        assert port.fetch_untested_actions() == []

    def test_legacy_actions_key_still_works(self):
        port = _port({"actions": ["ACTION3"]})
        assert port.fetch_untested_actions() == ["ACTION3"]

    def test_capability_missing_returns_empty(self):
        port = _port({"status": "capability_missing"})
        assert port.fetch_untested_actions() == []
