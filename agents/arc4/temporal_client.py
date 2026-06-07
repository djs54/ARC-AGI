"""Lazy Temporal.io client for ARC v2 workflow dispatch."""

from __future__ import annotations

import os
from typing import Any

_client_cache: Any = None


def is_temporal_enabled() -> bool:
    return os.environ.get("ARC_TEMPORAL_ENABLED", "0") in ("1", "true", "True")


async def _get_temporal_client():
    global _client_cache
    if _client_cache is not None:
        return _client_cache
    try:
        from temporalio.client import Client
        target = os.environ.get("ARC_TEMPORAL_TARGET", "localhost:7233")
        namespace = os.environ.get("ARC_TEMPORAL_NAMESPACE", "default")
        _client_cache = await Client.connect(target, namespace=namespace)
        return _client_cache
    except Exception:
        return None


async def start_arc_workflow(task_id: str, observation: dict, state_dict: dict) -> Any | None:
    if not is_temporal_enabled():
        return None
    client = await _get_temporal_client()
    if client is None:
        return None
    try:
        from .temporal_workflows import ArcPuzzleWorkflow
        handle = await client.start_workflow(
            ArcPuzzleWorkflow.run,
            {"task_id": task_id, "observation": observation, "state": state_dict},
            id=f"arc-puzzle-{task_id}",
            task_queue=os.environ.get("ARC_TEMPORAL_TASK_QUEUE", "arc-agent"),
        )
        return handle
    except Exception:
        return None


async def get_run_result(workflow_id: str) -> dict | None:
    client = await _get_temporal_client()
    if client is None:
        return None
    try:
        handle = client.get_workflow_handle(workflow_id)
        return await handle.result()
    except Exception:
        return None
