"""A164: graph evidence must be scoped by game_id, not the manifest's static
per-slot task_id.

Live evidence (this session's own artifacts): six different games
(ls20-9607627b, sk48-d8078629, r11l-495a7899, ar25-0c556536, tu93-0768757b,
sp80-589a99af) all shared task_id="arc_eval_001", because tasks_manifest.json
assigns a fixed task_id per slot and A149's live-catalog sync only remaps
game_id, never task_id. Every --live-smoke run (default --num-puzzles 1)
lands in slot 1, so the graph's ActionFact/Hypothesis nodes for that slot
pooled falsification/confidence evidence across unrelated games.

Fix: arc_runtime/bundle.py::build_arc_v2_bundle scopes the ArcGraphQueryPort
specifically by game_id (falling back to task_id only if game_id is unset),
while leaving telemetry's task_id (artifact labeling) and session_id/Temporal
workflow id (unrelated identity concerns) untouched.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arc_runtime.bundle import build_arc_v2_bundle
from arc_runtime.game_session import ArcV2GameSession


@dataclass
class _FakeBrainClient:
    calls: list[tuple[str, dict]] = field(default_factory=list)

    def call_tool(self, name: str, payload: dict) -> dict:
        self.calls.append((name, dict(payload)))
        return {}


class _FakeGameSession(ArcV2GameSession):
    def __init__(self) -> None:  # noqa: super-init-not-called -- test double, doesn't need the real harness/session wiring
        pass


def _build(task_id: str, game_id: str, brain_client: _FakeBrainClient):
    return build_arc_v2_bundle(
        task_id=task_id,
        game_id=game_id,
        game_title="Test Game",
        game_tags=(),
        brain_client=brain_client,
        session_id="session-1",
        append_snapshot=None,
        game_session=_FakeGameSession(),
        world_model_eval=False,
        max_cycles=1,
        llm_client=None,
    )


def test_graph_port_scoped_by_game_id_not_manifest_task_id():
    brain_client = _FakeBrainClient()
    bundle = _build(task_id="arc_eval_001", game_id="ls20-9607627b", brain_client=brain_client)

    assert bundle.graph_port.task_id == "ls20-9607627b"


def test_two_different_games_get_different_graph_task_ids():
    brain_client_a = _FakeBrainClient()
    brain_client_b = _FakeBrainClient()
    bundle_a = _build(task_id="arc_eval_001", game_id="ls20-9607627b", brain_client=brain_client_a)
    bundle_b = _build(task_id="arc_eval_001", game_id="sk48-d8078629", brain_client=brain_client_b)

    # Same manifest task_id (both slot 1), different games -> must not share
    # a graph identity.
    assert bundle_a.graph_port.task_id != bundle_b.graph_port.task_id
    assert bundle_a.graph_port.task_id == "ls20-9607627b"
    assert bundle_b.graph_port.task_id == "sk48-d8078629"


def test_same_game_replayed_shares_graph_task_id():
    brain_client_a = _FakeBrainClient()
    brain_client_b = _FakeBrainClient()
    bundle_a = _build(task_id="arc_eval_001", game_id="ls20-9607627b", brain_client=brain_client_a)
    bundle_b = _build(task_id="arc_eval_001", game_id="ls20-9607627b", brain_client=brain_client_b)

    # Replaying the same game should still pool evidence (intended,
    # persistent per-game learning) -- only cross-game pooling is the bug.
    assert bundle_a.graph_port.task_id == bundle_b.graph_port.task_id == "ls20-9607627b"


def test_missing_game_id_falls_back_to_task_id():
    brain_client = _FakeBrainClient()
    bundle = _build(task_id="arc_eval_001", game_id="", brain_client=brain_client)

    assert bundle.graph_port.task_id == "arc_eval_001"


def test_telemetry_task_id_unaffected_by_the_fix():
    """Telemetry/artifact labeling keeps the manifest task_id -- only the
    graph port's scoping identity changes. Avoids changing artifact shape
    for unrelated consumers."""
    brain_client = _FakeBrainClient()
    bundle = _build(task_id="arc_eval_001", game_id="ls20-9607627b", brain_client=brain_client)

    assert bundle.telemetry.task_id == "arc_eval_001"
