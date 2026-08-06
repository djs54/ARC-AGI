# A-128 — Temporal Infrastructure, Client, and Worker for ARC v2

Card: backlog/A128.md

## Summary

Stand up the Temporal.io infrastructure layer for ARC v2: optional dependency, lazy client, standalone worker, and Docker Compose for local dev. This is the foundation slice — no workflow logic changes yet.

## Implementation Approach

### 1. Optional Dependency

In `pyproject.toml`, add an extras group so Temporal is opt-in:

```toml
[project.optional-dependencies]
temporal = ["temporalio>=1.7.0"]
```

All Temporal imports in production code must be guarded behind try/except or conditional imports so the base install (`pip install -e .`) continues to work without `temporalio`.

### 2. Temporal Client (`agents/arc4/temporal_client.py`)

Port the Sandbox pattern from `ecosystem/orchestrator/temporal_client.py`:

```python
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
    from .temporal_workflows import ArcPuzzleWorkflow
    handle = await client.start_workflow(
        ArcPuzzleWorkflow.run,
        {"task_id": task_id, "observation": observation, "state": state_dict},
        id=f"arc-puzzle-{task_id}",
        task_queue=os.environ.get("ARC_TEMPORAL_TASK_QUEUE", "arc-agent"),
    )
    return handle


async def get_run_result(workflow_id: str) -> dict | None:
    client = await _get_temporal_client()
    if client is None:
        return None
    handle = client.get_workflow_handle(workflow_id)
    try:
        return await handle.result()
    except Exception:
        return None
```

Key design choices matching Sandbox:
- Lazy cached connection (process-wide singleton)
- Silent `None` return on connection failure (enables inline fallback)
- Feature flag read at call time, not import time
- No Temporal imports at module level

### 3. Temporal Worker (`agents/arc4/temporal_worker.py`)

```python
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
    )

    target = os.environ.get("ARC_TEMPORAL_TARGET", "localhost:7233")
    namespace = os.environ.get("ARC_TEMPORAL_NAMESPACE", "default")
    task_queue = os.environ.get("ARC_TEMPORAL_TASK_QUEUE", "arc-agent")

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
```

### 4. Docker Compose (`docker-compose.temporal.yml`)

```yaml
version: "3.8"
services:
  temporal-postgresql:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: temporal
      POSTGRES_PASSWORD: temporal
      POSTGRES_DB: temporal
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U temporal"]
      interval: 5s
      timeout: 3s
      retries: 10

  temporal:
    image: temporalio/auto-setup:latest
    depends_on:
      temporal-postgresql:
        condition: service_healthy
    environment:
      DB: postgres12
      DB_PORT: "5432"
      POSTGRES_USER: temporal
      POSTGRES_PWD: temporal
      POSTGRES_SEEDS: temporal-postgresql
    ports:
      - "7233:7233"

  temporal-ui:
    image: temporalio/ui:latest
    depends_on:
      - temporal
    environment:
      TEMPORAL_ADDRESS: temporal:7233
    ports:
      - "8233:8080"
```

### 5. Makefile Targets

```makefile
temporal-up:
	docker compose -f docker-compose.temporal.yml up -d

temporal-down:
	docker compose -f docker-compose.temporal.yml down
```

## Tests to Add

1. **`tests/test_a128_temporal_client.py`**
   - `test_temporal_disabled_by_default` — `is_temporal_enabled()` returns `False`
   - `test_start_workflow_returns_none_when_disabled` — `start_arc_workflow()` returns `None` without trying to connect
   - `test_temporal_imports_optional` — importing `agents.arc4` works without `temporalio` installed

## Validation Commands

```bash
make test-a
pytest tests/test_a128_temporal_client.py -v
python -c "from agents.arc4 import workflow; print('arc4 imports clean')"
```

## Assumptions

- Temporal server is local-only for now (no cloud deployment)
- The worker runs as a separate process, same machine
- Feature flag defaults OFF — zero behavior change without opt-in
- The Sandbox Temporal pattern (inline fallback) is the proven design to follow
