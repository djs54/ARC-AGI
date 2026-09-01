"""Tests for A231: arc_runtime/bundle.py::_readiness_gate wiring
fetch_untested_actions() (A135) into readiness_status()/probe selection,
exercised through the REAL bundle-construction path (build_arc_v2_bundle),
not a test-local replica of the closure.

Covers the pieces unique to bundle.py's own new code (not already covered
by the pure-function tests in test_a224_readiness_gate.py/
test_a224_readiness_probe_selection.py or the workflow-routing tests in
test_a224_workflow_readiness_integration.py):
  - `available_actions` is read straight off perception.observation, mirroring
    workflow.py's own existing stall-check pattern.
  - fetch_untested_actions() is called WITH that available_actions list (the
    A231 fix -- see test_a231_fetch_untested_actions_available_param.py for
    why passing it matters).
  - "ACTION6" is filtered out of the untested-actions result even if the
    server returns it (click coverage is tracked at the entity level).
  - a returned action not in the observation's real available-action set is
    filtered out defensively.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.annatar_state_machine import ReadinessStatus
from agents.arc4.graph_queries import ARC_V2_TOOL_NAMES
from agents.arc4.types import PerceptionSnapshot, WorkflowState
from arc_runtime.bundle import build_arc_v2_bundle
from arc_runtime.game_session import ArcV2GameSession


@dataclass
class _FakeBrainClient:
    untested_response: dict[str, Any]
    calls: list[tuple[str, dict]] = field(default_factory=list)

    def call_tool(self, name: str, payload: dict) -> dict:
        self.calls.append((name, dict(payload)))
        if name == ARC_V2_TOOL_NAMES["fetch_untested_actions"]:
            return self.untested_response
        return {}


class _FakeGameSession(ArcV2GameSession):
    def __init__(self) -> None:  # noqa: super-init-not-called -- test double
        pass


def _build(brain_client: _FakeBrainClient, *, max_cycles: int = 30):
    return build_arc_v2_bundle(
        task_id="arc_eval_001",
        game_id="game-a231",
        game_title="Test Game",
        game_tags=(),
        brain_client=brain_client,
        session_id="session-1",
        append_snapshot=None,
        game_session=_FakeGameSession(),
        world_model_eval=False,
        max_cycles=max_cycles,
        llm_client=None,
    )


def _perception(available_actions: list[str]) -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"available_actions": available_actions},
        grid_hash="h1",
        entities=(),
    )


class TestBundleReadinessGateUntestedActions:
    def test_calls_fetch_untested_actions_with_observation_available_actions(self):
        brain_client = _FakeBrainClient(untested_response={"untested": [], "tested": []})
        bundle = _build(brain_client)
        perception = _perception(["ACTION1", "ACTION2", "ACTION6"])
        state = WorkflowState()

        bundle.dependencies.readiness_gate(state, perception)

        untested_calls = [c for c in brain_client.calls if c[0] == ARC_V2_TOOL_NAMES["fetch_untested_actions"]]
        assert len(untested_calls) == 1
        assert untested_calls[0][1].get("available_actions") == ["ACTION1", "ACTION2", "ACTION6"]

    def test_untested_non_click_action_drives_not_ready_and_probe_candidate(self):
        brain_client = _FakeBrainClient(
            untested_response={"untested": ["ACTION2"], "tested": ["ACTION1", "ACTION6"]},
        )
        bundle = _build(brain_client)
        perception = _perception(["ACTION1", "ACTION2", "ACTION6"])
        state = WorkflowState()

        result = bundle.dependencies.readiness_gate(state, perception)

        payload = result.payload
        assert payload["status"] == ReadinessStatus.NOT_READY
        assert payload["untested_non_click_actions"] == ["ACTION2"]
        assert payload["probe_candidate"] is not None
        assert payload["probe_candidate"].action_id == "ACTION2"
        assert payload["probe_candidate"].payload == {}
        assert payload["probe_candidate"].metadata.get("readiness_probe_kind") == "action"

    def test_action6_filtered_out_of_untested_actions_even_if_server_returns_it(self):
        """Click coverage is already tracked at the entity level via
        entity_domains -- ACTION6 must never double-count as an untested
        non-click action, even if the server's own set-difference includes
        it (e.g. before any click has ever been attempted)."""
        brain_client = _FakeBrainClient(
            untested_response={"untested": ["ACTION6"], "tested": []},
        )
        bundle = _build(brain_client)
        perception = _perception(["ACTION6"])
        state = WorkflowState()

        result = bundle.dependencies.readiness_gate(state, perception)

        # No entities at all (blank perception) and ACTION6 filtered out ->
        # nothing left blocking readiness.
        assert result.payload["untested_non_click_actions"] == []
        assert result.payload["status"] == ReadinessStatus.READY

    def test_action_not_in_observation_available_actions_is_filtered_out(self):
        """Defensive filter against the observation's real available-action
        set, per the plan -- a stale/phantom action_id from the graph must
        not drive the gate."""
        brain_client = _FakeBrainClient(
            untested_response={"untested": ["ACTION9"], "tested": []},
        )
        bundle = _build(brain_client)
        perception = _perception(["ACTION1", "ACTION6"])
        state = WorkflowState()

        result = bundle.dependencies.readiness_gate(state, perception)

        assert result.payload["untested_non_click_actions"] == []
        assert result.payload["status"] == ReadinessStatus.READY

    def test_no_available_actions_in_observation_skips_the_graph_call_entirely(self):
        """No available_actions in the observation at all -- nothing to ask
        the graph about, and no untested-actions call should be made."""
        brain_client = _FakeBrainClient(untested_response={"untested": ["ACTION1"], "tested": []})
        bundle = _build(brain_client)
        perception = PerceptionSnapshot(observation={}, grid_hash="h1", entities=())
        state = WorkflowState()

        result = bundle.dependencies.readiness_gate(state, perception)

        untested_calls = [c for c in brain_client.calls if c[0] == ARC_V2_TOOL_NAMES["fetch_untested_actions"]]
        assert untested_calls == []
        assert result.payload["untested_non_click_actions"] == []
        assert result.payload["status"] == ReadinessStatus.READY
