"""Standalone Temporal worker for ARC v2 puzzle workflows."""

from __future__ import annotations

import asyncio
import os


async def main():
    from temporalio.client import Client
    from temporalio.worker import Worker
    from .temporal_workflows import ArcPuzzleWorkflow
    from .temporal_activities import (
        perceive_activity,
        resolve_activity,
        plan_activity,
        vet_activity,
        execute_activity,
        evaluate_activity,
        register_phases,
    )

    target = os.environ.get("ARC_TEMPORAL_TARGET", "localhost:7233")
    namespace = os.environ.get("ARC_TEMPORAL_NAMESPACE", "default")
    task_queue = os.environ.get("ARC_TEMPORAL_TASK_QUEUE", "arc-agent")

    # Register phase callables for activities to access
    # TODO: Initialize actual phase callables based on configuration
    register_phases({
        "perceive": lambda state, obs: None,  # Placeholder
        "resolve": lambda state, perc: None,  # Placeholder
        "plan": lambda state, perc, goal: None,  # Placeholder
        "vet": lambda state, perc, goal, plan: None,  # Placeholder
        "execute": lambda state, perc, goal, vet: None,  # Placeholder
        "evaluate": lambda state, perc, goal, exec: None,  # Placeholder
    })

    client = await Client.connect(target, namespace=namespace)
    worker = Worker(
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
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
