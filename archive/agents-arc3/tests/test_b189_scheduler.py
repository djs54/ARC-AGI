import asyncio
import pytest

from agents.arc3.scheduler import PuzzleScheduler


@pytest.mark.asyncio
async def test_ordering_easy_first():
    scheduler = PuzzleScheduler(concurrency=1, skip_solved=False)
    tasks = [
        {"task_id": "t_hard", "grid_size": 60},
        {"task_id": "t_easy", "grid_size": 5},
    ]

    ordered = await scheduler.prepare(tasks)
    ids = [t.get("task_id") for t in ordered]
    assert ids == ["t_easy", "t_hard"]


@pytest.mark.asyncio
async def test_skip_previously_solved():
    scheduler = PuzzleScheduler(concurrency=1, skip_solved=True)

    # stub the DB check to simulate that 't1' was solved
    async def fake_check(task_id):
        return task_id == "t1"

    scheduler._check_previously_solved = fake_check

    tasks = [{"task_id": "t1", "grid_size": 10}, {"task_id": "t2", "grid_size": 8}]
    ordered = await scheduler.prepare(tasks)
    ids = [t.get("task_id") for t in ordered]
    assert "t1" not in ids


@pytest.mark.asyncio
async def test_health_check_aborts_batch(monkeypatch):
    scheduler = PuzzleScheduler(concurrency=1, skip_solved=False)

    # health check always fails
    async def bad_health():
        return False

    scheduler._health_check = bad_health
    scheduler._health_retry_delay = 0

    called = 0

    async def run_fn(task):
        nonlocal called
        called += 1
        return {"task_id": task.get("task_id")}

    tasks = [{"task_id": "a"}, {"task_id": "b"}]
    results = await scheduler.run_batch(tasks, run_fn)
    assert called == 0
    assert results == []


@pytest.mark.asyncio
async def test_eta_reported_after_first_puzzle():
    scheduler = PuzzleScheduler(concurrency=1, skip_solved=False)

    async def run_fn(task):
        await asyncio.sleep(0)
        return {"task_id": task.get("task_id")}

    tasks = [{"task_id": "x"}, {"task_id": "y"}]
    results = await scheduler.run_batch(tasks, run_fn)
    assert len(results) == 2
    assert scheduler.last_eta is not None
