"""Tests for A231's Track A finding: ArcGraphQueryPort.fetch_untested_actions()
never sent `available_actions` to the server tool, so the server's own
`untested = [a for a in available if a not in tested]` (arc_queries.py::
arc_get_untested_actions, hippocampy) always computed against an empty
`available` list -- `untested` was ALWAYS `[]` in production regardless of
what had actually been tested. Confirmed live against the real hippocampy
graph before writing this fix (see backlog/A231.md Outcome for the raw
evidence: a genuinely fresh task_id returned `{"untested": [], ...}` without
the param and the full requested list with it).

Fixed by giving the client method an optional `available_actions` parameter.
`available_actions=None` (the default) preserves the exact pre-A231 payload
(`{"task_id": ...}` only) -- plan_generator.py's own pre-existing consumer
(~line 558) calls this with no arguments and must see byte-for-byte
unchanged behavior, per this card's explicit "don't touch that consumer"
boundary. Only the new readiness-gate call site supplies the parameter.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.graph_queries import ArcGraphQueryPort


class _RecordingBrainClient:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, dict(payload)))
        return self._result


def _port(brain_client: _RecordingBrainClient) -> ArcGraphQueryPort:
    return ArcGraphQueryPort(brain_client, task_id="task-1", session_id="session-1", strict=False)


class TestFetchUntestedActionsAvailableActionsParam:
    def test_no_args_omits_available_actions_key_byte_for_byte(self):
        """Pre-A231 call sites (plan_generator.py ~line 558) call this with
        no arguments -- the payload sent to the tool must be exactly what it
        was before this card, or that consumer's (already degraded, but
        unrelated to this card) behavior would silently change."""
        client = _RecordingBrainClient({"untested": [], "tested": []})
        port = _port(client)

        port.fetch_untested_actions()

        assert client.calls == [("arc_get_untested_actions", {"task_id": "task-1"})]

    def test_available_actions_passed_through_when_supplied(self):
        """The new readiness-gate call site (A231) supplies this so the
        server can compute a real untested = available - tested set
        difference instead of always seeing an empty `available`."""
        client = _RecordingBrainClient({"untested": ["ACTION3"], "tested": ["ACTION1"]})
        port = _port(client)

        result = port.fetch_untested_actions(available_actions=["ACTION1", "ACTION3"])

        assert client.calls == [
            ("arc_get_untested_actions", {"task_id": "task-1", "available_actions": ["ACTION1", "ACTION3"]}),
        ]
        assert result == ["ACTION3"]

    def test_empty_available_actions_list_also_omits_the_key(self):
        """An empty sequence is falsy -- treated the same as None, not sent
        as an empty list (which the server would also just treat as "nothing
        available", but omitting it keeps the payload shape consistent)."""
        client = _RecordingBrainClient({"untested": [], "tested": []})
        port = _port(client)

        port.fetch_untested_actions(available_actions=[])

        assert client.calls == [("arc_get_untested_actions", {"task_id": "task-1"})]
