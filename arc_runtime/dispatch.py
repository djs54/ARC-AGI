from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from agents.arc4.graph_queries import ArcGraphQueryPort
from agents.arc4.temporal_client import is_temporal_enabled
from agents.arc4.types import WorkflowRunResult, WorkflowState
from arc_runtime.bundle import build_arc_v2_bundle
from arc_runtime.game_session import ArcV2GameSession
from arc_runtime.llm import create_llm_client
from benchmarks.arc3.adapter import ARC3Adapter, NoOpBrainClient


logger = logging.getLogger(__name__)


def resume_or_start_attempt(
    task_id: str,
    graph_port: Any,
    fetch_real_observation: Callable[[], Any],
) -> dict[str, Any]:
    """A204 / spec section 7: startup resume check + real-observation
    reconciliation, run once per task attempt before a fresh WorkflowState
    is used. Never re-sends an action, and never trusts graph bookkeeping
    alone in the ambiguous "action_sent=True,
    action_confirmed_by_observation=False" window -- it always asks the
    real, live ARC API first, via `fetch_real_observation`.

    `fetch_real_observation` is called AT MOST ONCE, and only when the
    ambiguous window is actually detected -- it is the harness's own
    `game_session.open()` bound method (the only "current game observation"
    call this runtime exposes; see arc_runtime/game_session.py -- there is
    no separate non-destructive peek endpoint). Callers must still call it
    themselves afterward when this function's "real_observation" key comes
    back None, so the live API call happens exactly once total per attempt
    regardless of path -- this function never constructs a second client.

    Design note (deliberately conservative, see A204 Resolution for the
    full reasoning): if `fetch_real_observation` itself raises during the
    ambiguous window, this function does NOT catch it and silently guess
    confirmed=True or confirmed=False -- either guess risks the exact
    failure mode this card exists to prevent, so the exception is left to
    propagate and the caller must treat attempt startup as blocked.

    Returns:
        {"resumed": bool, "step_index": int, "thread_id": Any | None,
         "real_observation": Any | None}
    """
    result: dict[str, Any] = {"resumed": False, "step_index": 0, "thread_id": None, "real_observation": None}

    if graph_port is None:
        return result

    start_or_resume = getattr(graph_port, "start_or_resume_thread", None)
    if start_or_resume is None:
        return result

    try:
        thread_result = start_or_resume(anchor_ref=task_id, anchor_type="goal")
    except Exception:
        return result

    if not isinstance(thread_result, dict) or not thread_result.get("resumed"):
        if isinstance(thread_result, dict):
            result["thread_id"] = thread_result.get("thread_id")
        return result

    last_cycle = thread_result.get("last_cycle")
    result["resumed"] = True
    result["thread_id"] = thread_result.get("thread_id")
    result["step_index"] = int(last_cycle.get("step", 0)) if last_cycle else 0

    if last_cycle and last_cycle.get("action_sent") and not last_cycle.get("action_confirmed_by_observation"):
        # Ambiguous crash window -- never assume, always check the real API
        # first. Intentionally not wrapped in try/except: see the design
        # note in this function's docstring.
        real_observation = fetch_real_observation()
        result["real_observation"] = real_observation

        action_confirmed = _effect_visible_in_observation(last_cycle, real_observation)
        confirm_cycle = getattr(graph_port, "confirm_cycle", None)
        if confirm_cycle is not None:
            try:
                confirm_cycle(last_cycle.get("cycle_id"), decision="resumed", confirmed=action_confirmed)
            except Exception:
                pass  # a failed confirm write must not crash attempt startup

    return result


def _effect_visible_in_observation(last_cycle: dict[str, Any], real_observation: Any) -> bool:
    """Best-effort: without a recoverable predicted-effect record on the
    cycle itself, treat any real observation successfully retrieved as
    confirmation that resuming from the CURRENT real state (not a replay)
    is safe -- the goal is never re-send the action on a guess, not to
    perfectly reconstruct what happened. Refine this if a predicted effect
    becomes available on the Cycle record in a later card."""
    return real_observation is not None


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

    # A204: construct the graph port before the live game session/observation
    # fetch so the startup resume/reconciliation check (spec section 7) can
    # run first. graph_task_id mirrors build_arc_v2_bundle's own A164 scoping
    # exactly (game_id when known, else the manifest task_id) so this is the
    # same graph identity the rest of the attempt's evidence will be scoped
    # under -- build_arc_v2_bundle below reuses this exact instance rather
    # than constructing a second one.
    graph_task_id = game_id or task.task_id
    graph_port = ArcGraphQueryPort(brain_client, task_id=graph_task_id, session_id=session_id, strict=False)

    game_session = ArcV2GameSession(runner.harness, game_id=game_id, card_id=card_id, real_api=runner.real_api)

    # A204: resume_or_start_attempt calls game_session.open() itself (via
    # fetch_real_observation) only in the ambiguous crash window, and never
    # more than once. In the common case (no ambiguous in-flight cycle, or
    # no graph configured) it makes no live API call at all, so the
    # unconditional game_session.open() below is this attempt's one and
    # only RESET call -- exactly as before this card.
    resume_info = resume_or_start_attempt(task.task_id, graph_port, game_session.open)
    initial_frame = (
        resume_info["real_observation"] if resume_info["real_observation"] is not None else game_session.open()
    )
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
        graph_port=graph_port,
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
        # A204: deliberately NOT seeding WorkflowState(step_index=...) from
        # resume_info even when resume_info["resumed"] is True. This harness
        # always issues a fresh RESET via game_session.open() at the start of
        # every task attempt (there is no live mid-episode session resume
        # capability today), so a resumed thread's step_index belongs to a
        # now-discarded prior episode, not this new one -- seeding it would
        # make check_budget's step ceiling fire against the wrong episode.
        # The safety-critical behavior this card delivers (never trusting an
        # ambiguous action_sent flag without checking the real observation,
        # and always closing out the graph's cycle bookkeeping via
        # confirm_cycle above) is fully realized without this seed. See the
        # A204 backlog card's Resolution section for the full reasoning.
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
