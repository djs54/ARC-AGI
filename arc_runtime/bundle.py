from __future__ import annotations

import asyncio
import dataclasses
import enum
import inspect
import json
import logging
from typing import Any, Callable, Mapping

from agents.arc4.evaluator import Evaluator
from agents.arc4.executor import Executor
from agents.arc4.goal_resolver import GoalResolver, GoalResolverLimits
from agents.arc4.graph_queries import ArcGraphQueryPort
from agents.arc4.perceive import PerceiveAgent
from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.plan_vetter import PlanVetter
from agents.arc4.ports import WorkflowDependencies
from agents.arc4.telemetry import ArcV2Telemetry
from agents.arc4.types import PhaseResult, PhaseStatus, WorkflowPhase
from agents.arc4.workflow import WorkflowLimits, WorkflowOrchestrator
from arc_runtime.game_session import ArcV2GameSession

logger = logging.getLogger(__name__)


@dataclasses.dataclass(slots=True)
class ArcV2Bundle:
    graph_port: ArcGraphQueryPort
    telemetry: ArcV2Telemetry
    dependencies: WorkflowDependencies
    orchestrator: WorkflowOrchestrator
    llm_port: Any | None = None


class SyncLLMPortAdapter:
    def __init__(self, llm_client: Any) -> None:
        self._llm_client = llm_client
        self.total_tokens_in = 0
        self.total_tokens_out = 0

    @staticmethod
    def _json_default(obj):
        if dataclasses.is_dataclass(obj):
            return dataclasses.asdict(obj)
        if isinstance(obj, enum.Enum):
            return obj.value
        to_dict = getattr(obj, "to_dict", None)
        if callable(to_dict):
            return to_dict()
        return str(obj)

    def chat(self, messages: list[Any]) -> str:
        if self._llm_client is None:
            raise RuntimeError("LLM client is not configured")

        message_dicts = [
            {"role": message.role, "content": message.content}
            for message in messages
        ]

        achat = getattr(self._llm_client, "achat", None)
        if achat is not None:
            try:
                try:
                    result = asyncio.get_event_loop().run_until_complete(achat(message_dicts))
                except RuntimeError:
                    import concurrent.futures

                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(asyncio.run, achat(message_dicts))
                        result = future.result(timeout=60)
                prompt_text = " ".join(m.get("content", "") for m in message_dicts)
                self.total_tokens_in += len(prompt_text) // 4
                response_str = str(result.content if hasattr(result, "content") else result)
                self.total_tokens_out += len(response_str) // 4
                return str(result.content) if hasattr(result, "content") else str(result)
            except Exception as exc:
                # Covers failures from either the direct get_event_loop() path or the
                # RuntimeError/ThreadPoolExecutor fallback above (e.g. the underlying
                # achat() call itself raising inside the thread pool) — any of these
                # must fall through to the sync-method loop below, not propagate.
                logger.warning("SyncLLMPortAdapter: achat call failed, falling back to sync methods: %s", exc)

        prompt = "\n".join(f"{m.role}: {m.content}" for m in messages)
        for method_name in ("generate", "complete", "chat"):
            method = getattr(self._llm_client, method_name, None)
            if method is None:
                continue
            try:
                result = method(prompt)
                if inspect.isawaitable(result):
                    result = asyncio.run(result)
                self.total_tokens_in += len(prompt) // 4
                response_str = str(result)
                self.total_tokens_out += len(response_str) // 4
                return response_str
            except Exception as exc:
                logger.warning("SyncLLMPortAdapter: %s() call failed: %s", method_name, exc)
                return ""

        logger.warning("SyncLLMPortAdapter: llm_client %r has no achat/generate/complete/chat method", self._llm_client)
        return ""


def build_arc_v2_bundle(
    *,
    task_id: str,
    game_id: str,
    game_title: str,
    game_tags: tuple[str, ...],
    brain_client: Any,
    session_id: str,
    append_snapshot: Callable[[dict[str, Any]], None] | None,
    game_session: ArcV2GameSession,
    world_model_eval: bool,
    max_cycles: int,
    llm_client: Any | None = None,
) -> ArcV2Bundle:
    # A164: scope graph evidence by game_id, not the manifest's static per-slot
    # task_id. tasks_manifest.json assigns a fixed task_id per slot (e.g.
    # "arc_eval_001") and A149's live-catalog sync only remaps game_id, never
    # task_id -- so every --live-smoke run landing in the same manifest slot
    # (the default for --num-puzzles 1) shared one graph identity regardless
    # of which actual ARC game was played, pooling falsification/confidence
    # evidence across semantically unrelated games. game_id is unique per
    # game variant (ARC rotates hash-suffixed ids) and stable across replays
    # of the same game, which is the identity graph evidence should actually
    # be scoped by. Falls back to task_id if game_id is ever unset.
    graph_task_id = game_id or task_id
    graph_port = ArcGraphQueryPort(brain_client, task_id=graph_task_id, session_id=session_id, strict=False)
    telemetry = ArcV2Telemetry(
        task_id=task_id,
        game_id=game_id,
        game_title=game_title,
        game_tags=game_tags,
        append_snapshot=append_snapshot,
        world_model_eval=world_model_eval,
    )
    llm_port = SyncLLMPortAdapter(llm_client) if llm_client is not None else None

    perceive = telemetry.wrap_phase("perceive", PerceiveAgent(graph_port).perceive)
    resolve_agent = GoalResolver(GoalResolverLimits())
    resolve = telemetry.wrap_phase(
        "resolve",
        lambda state, perception: resolve_agent.resolve(state, perception, graph_port=graph_port, llm_port=llm_port),
    )

    plan_agent = PlanGenerator(PlanGeneratorLimits())
    plan = telemetry.wrap_phase(
        "plan",
        lambda state, perception, goal: plan_agent.generate(state, perception, goal, graph_port=graph_port, llm_port=llm_port),
    )
    vet = telemetry.wrap_phase("vet", PlanVetter(graph_port=graph_port).vet)
    execute_agent = Executor(transport=game_session)
    execute = telemetry.wrap_phase(
        "execute",
        lambda state, perception, goal, vet_decision: execute_agent.execute(
            state,
            vet_decision.candidate or vet_decision.alternative,
            {
                "game_id": game_id,
                "guid": getattr(game_session, "_guid", None),
                "step": state.step_index,
                "session_id": session_id,
                "state": perception.observation.get("state") if isinstance(perception.observation, Mapping) else None,
            },
        )
        if (vet_decision.candidate or vet_decision.alternative) is not None
        else PhaseResult(
            phase=WorkflowPhase.EXECUTE,
            status=PhaseStatus.CRASH,
            reason="missing_vetted_candidate",
            payload=None,
        ),
    )
    evaluate = telemetry.wrap_phase("evaluate", Evaluator(graph_query_port=graph_port).evaluate)

    dependencies = WorkflowDependencies(
        perceive=perceive,
        resolve=resolve,
        plan=plan,
        vet=vet,
        execute=execute,
        evaluate=evaluate,
    )
    orchestrator = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=max_cycles))
    return ArcV2Bundle(graph_port=graph_port, telemetry=telemetry, dependencies=dependencies, orchestrator=orchestrator, llm_port=llm_port)
