from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.graph_queries import ARC_V2_TOOL_NAMES, ArcGraphQueryPort
from agents.arc4.types import GoalHypothesis, PerceptionSnapshot, PlanCandidate, PlanningResult, ResolvedGoal, VetDecision, WorkflowPhase, WorkflowState
from run_single_puzzle import _ArcV2GameSession, _build_arc_v2_bundle, build_arg_parser


class FakeBrainClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict | None = None, timeout: float | None = None) -> dict:
        del timeout
        payload = dict(arguments or {})
        self.calls.append((name, payload))
        if name == ARC_V2_TOOL_NAMES["ingest_perception"]:
            return {"status": "ok", "snapshot_id": f"{payload.get('task_id')}_step{payload.get('step', 0)}", "entity_count": len(payload.get("entities", [])), "delta_from_previous": None}
        if name == ARC_V2_TOOL_NAMES["fetch_goal_evidence"]:
            return {"goals": [{"id": "goal-a", "description": "Match red cluster", "confidence": 0.6, "evidence": ["graph"]}]}
        if name == ARC_V2_TOOL_NAMES["fetch_game_context"]:
            return {"summary": "Current game context"}
        if name == ARC_V2_TOOL_NAMES["fetch_mechanic_priors"]:
            return {"priors": []}
        if name == ARC_V2_TOOL_NAMES["record_action_effect"]:
            return {"status": "ok", "effect_id": "effect-1", "fact_id": "fact-1"}
        if name == ARC_V2_TOOL_NAMES["update_goal_confidence"]:
            return {"status": "ok", "goal_id": payload.get("goal_id"), "gated_confidence": payload.get("new_confidence")}
        if name == ARC_V2_TOOL_NAMES["record_reward_prediction_error"]:
            return {"status": "ok", "prediction_error": payload.get("reward_prediction_error", 0.0), "direction": "flat"}
        return {"status": "ok"}


class FakeGameTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, dict]] = []
        self._guid = "guid-1"

    def execute_action(self, action_id: str, payload: dict, context: dict) -> dict:
        self.calls.append((action_id, dict(payload), dict(context)))
        return {
            "observation": {
                "grid": [[1, 1], [0, 0]],
                "state": "NOT_FINISHED",
                "available_actions": ["ACTION6", "ACTION7"],
            },
            "did_progress": False,
            "actual_effect": "moved",
        }


class FakeLLMClient:
    def generate(self, prompt: str) -> str:
        del prompt
        return '{"goal_id": "goal-a", "confidence": 0.8, "reason": "choose goal-a", "action_id": "ACTION7"}'


def test_build_arg_parser_supports_agent_version() -> None:
    parser = build_arg_parser()
    args = parser.parse_args(["--agent-version", "v2"])
    assert args.agent_version == "v2"


def test_graph_query_port_uses_b278_tool_names() -> None:
    brain_client = FakeBrainClient()
    port = ArcGraphQueryPort(brain_client, task_id="task-1", session_id="session-1")

    snapshot = PerceptionSnapshot(
        observation={"grid": [[1]], "state": "NOT_FINISHED"},
        grid_hash="hash-1",
        grid_shape=(1, 1),
    )
    result = port.ingest_perception(snapshot)

    assert result["tool"] == ARC_V2_TOOL_NAMES["ingest_perception"]
    assert brain_client.calls[0][0] == ARC_V2_TOOL_NAMES["ingest_perception"]


def test_graph_query_port_gracefully_handles_missing_tools_when_not_strict() -> None:
    class MissingToolBrainClient:
        async def call_tool(self, name: str, arguments: dict | None = None, timeout: float | None = None) -> dict:
            del arguments, timeout
            raise RuntimeError(f"Unknown method: {name}")

    port = ArcGraphQueryPort(MissingToolBrainClient(), task_id="task-1", session_id="session-1", strict=False)
    snapshot = PerceptionSnapshot(
        observation={"grid": [[1]], "state": "NOT_FINISHED"},
        grid_hash="hash-1",
        grid_shape=(1, 1),
    )

    records = port.fetch_goal_evidence(snapshot)

    assert records == []


def test_build_arc_v2_bundle_execute_phase_adapts_vet_candidate() -> None:
    brain_client = FakeBrainClient()
    transport = FakeGameTransport()
    bundle = _build_arc_v2_bundle(
        task_id="task-1",
        game_id="game-1",
        game_title="Game 1",
        game_tags=("mock",),
        brain_client=brain_client,
        session_id="session-1",
        append_snapshot=None,
        game_session=transport,
        world_model_eval=False,
        max_cycles=1,
        llm_client=FakeLLMClient(),
    )

    candidate = PlanCandidate(action_id="ACTION7", goal_id="goal-a", metadata={"args": {"x": 1, "y": 2}})
    vet = VetDecision(approved=True, candidate=candidate)
    perception = PerceptionSnapshot(
        observation={"grid": [[1]], "state": "NOT_FINISHED", "available_actions": ["ACTION6", "ACTION7"]},
        grid_hash="hash-1",
        grid_shape=(1, 1),
    )
    goal = ResolvedGoal(selected=GoalHypothesis(goal_id="goal-a", description="goal", confidence=0.6))

    result = bundle.dependencies.execute(WorkflowState(), perception, goal, vet)

    assert result.phase == WorkflowPhase.EXECUTE
    assert result.payload is not None
    assert result.payload.action_id == "ACTION7"
    assert transport.calls[0][0] == "ACTION7"
    assert transport.calls[0][1] == {"x": 1, "y": 2}


def test_arc_v2_telemetry_append_receives_phase_snapshots() -> None:
    brain_client = FakeBrainClient()
    emitted: list[dict] = []
    bundle = _build_arc_v2_bundle(
        task_id="task-1",
        game_id="game-1",
        game_title="Game 1",
        game_tags=("mock",),
        brain_client=brain_client,
        session_id="session-1",
        append_snapshot=emitted.append,
        game_session=FakeGameTransport(),
        world_model_eval=False,
        max_cycles=1,
        llm_client=FakeLLMClient(),
    )

    initial_observation = {"grid": [[1, 1], [0, 0]], "state": "NOT_FINISHED", "available_actions": ["ACTION6", "ACTION7"]}
    run_result = bundle.orchestrator.run(WorkflowState(), initial_observation)

    assert run_result.status.value in {"budget_exhausted", "terminated", "stalled"}
    assert emitted
    assert any(snapshot.get("snapshot_type") == "phase_transition" for snapshot in emitted)