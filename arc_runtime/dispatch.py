from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from agents.arc4.temporal_client import is_temporal_enabled
from agents.arc4.types import WorkflowRunResult, WorkflowState
from arc_runtime.bundle import build_arc_v2_bundle
from arc_runtime.game_session import ArcV2GameSession
from arc_runtime.llm import create_llm_client
from benchmarks.arc3.adapter import ARC3Adapter, NoOpBrainClient


logger = logging.getLogger(__name__)

# Guarded import for Temporal (optional dependency)
try:
    from agents.arc4.temporal_client import start_arc_workflow
    HAS_TEMPORAL = True
except ImportError:
    HAS_TEMPORAL = False


def run_arc_v2_task(task: Any, runner: Any, card_id: str, brain_client: Any, args: Any = None) -> dict[str, Any]:
    session_id = f"arc-v2-{task.task_id}-{int(time.time())}"
    game_id = str(getattr(task, "game_id", "unknown"))
    game_title = str(getattr(task, "arc_game_title", "") or "")
    game_tags = tuple(str(tag) for tag in (getattr(task, "arc_game_tags", []) or []))
    game_session = ArcV2GameSession(runner.harness, game_id=game_id, card_id=card_id, real_api=runner.real_api)
    initial_frame = game_session.open()
    logger.info("RAW initial_frame available_actions=%s", initial_frame.get("available_actions"))
    adapter = ARC3Adapter(NoOpBrainClient(), session_id=session_id, task_id=task.task_id)
    observation = adapter.normalize_observation(initial_frame)
    logger.info("NORMALIZED observation available_actions=%s", observation.get("available_actions"))

    bundle = build_arc_v2_bundle(
        task_id=task.task_id,
        game_id=game_id,
        game_title=game_title,
        game_tags=game_tags,
        brain_client=brain_client,
        session_id=session_id,
        append_snapshot=runner.append_live_snapshot,
        game_session=game_session,
        world_model_eval=runner.world_model_eval,
        max_cycles=int(runner.config.get("benchmark", {}).get("max_attempts_per_puzzle", 3) or 3),
        llm_client=create_llm_client(runner.config),
    )

    use_temporal = HAS_TEMPORAL and (is_temporal_enabled() or (args and getattr(args, "temporal", False)))

    if use_temporal:
        try:
            import asyncio as _asyncio
            from agents.arc4.temporal_activities import register_phases
            from agents.arc4.temporal_workflows import ArcPuzzleWorkflow
            from agents.arc4.temporal_activities import (
                perceive_activity,
                resolve_activity,
                plan_activity,
                vet_activity,
                execute_activity,
                evaluate_activity,
            )
            from temporalio.client import Client as TemporalClient
            from temporalio.worker import Worker as TemporalWorker

            register_phases(
                {
                    "perceive": bundle.dependencies.perceive,
                    "resolve": bundle.dependencies.resolve,
                    "plan": bundle.dependencies.plan,
                    "vet": bundle.dependencies.vet,
                    "execute": bundle.dependencies.execute,
                    "evaluate": bundle.dependencies.evaluate,
                }
            )

            async def _temporal_dispatch():
                import os

                target = os.environ.get("ARC_TEMPORAL_TARGET", "localhost:7233")
                namespace = os.environ.get("ARC_TEMPORAL_NAMESPACE", "default")
                task_queue = os.environ.get("ARC_TEMPORAL_TASK_QUEUE", "arc-agent")

                client = await TemporalClient.connect(target, namespace=namespace)

                async with TemporalWorker(
                    client,
                    task_queue=task_queue,
                    workflows=[ArcPuzzleWorkflow],
                    activities=[
                        perceive_activity,
                        resolve_activity,
                        plan_activity,
                        vet_activity,
                        execute_activity,
                        evaluate_activity,
                    ],
                ):
                    handle = await client.start_workflow(
                        ArcPuzzleWorkflow.run,
                        {
                            "state": WorkflowState().to_dict(),
                            "observation": observation,
                            "max_cycles": bundle.orchestrator._limits.max_cycles,
                        },
                        id=f"arc-puzzle-{task.task_id}",
                        task_queue=task_queue,
                    )
                    return await handle.result()

            _loop = _asyncio.new_event_loop()
            try:
                result_dict = _loop.run_until_complete(_temporal_dispatch())
                workflow_result = WorkflowRunResult.from_dict(result_dict)
                logger.info("Puzzle %s completed via Temporal workflow", task.task_id)
            finally:
                _loop.close()

        except Exception as exc:
            logger.warning("Temporal dispatch failed for %s: %s; falling back to inline execution", task.task_id, exc)
            workflow_result = bundle.orchestrator.run(WorkflowState(), observation)
    else:
        workflow_result = bundle.orchestrator.run(WorkflowState(), observation)

    if bundle.llm_port is not None:
        bundle.telemetry.tokens_input = bundle.llm_port.total_tokens_in
        bundle.telemetry.tokens_output = bundle.llm_port.total_tokens_out

    result = bundle.telemetry.build_final_result(workflow_result)
    game_session.close()
    return result


async def run_arc_v2_batch(runner: Any, brain_client: Any, card_id: str, args: Any = None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for task in runner.tasks:
        result = await asyncio.to_thread(run_arc_v2_task, task, runner, card_id, brain_client, args)
        results.append(result)
    return results
