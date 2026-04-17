# Plan for B189 — Puzzle Scheduler

## Card Metadata

- **Card ID**: B189
- **Priority**: P2
- **Dependencies**: B180 (budget), B184 (circuit breaker)

## Summary

Replace the simple for-loop in `DurableARCRunner.run()` with a scheduler that orders puzzles by estimated difficulty, skips previously-solved ones, runs health checks, and supports configurable concurrency.

## Technical Approach

### Step 1: Create agents/arc3/scheduler.py

```python
@dataclass
class PuzzleTask:
    task_id: str
    estimated_difficulty: float  # 0.0 (easy) to 1.0 (hard)
    previously_solved: bool
    grid_size: int
    archetype_hint: Optional[str]

class PuzzleScheduler:
    def __init__(self, concurrency: int = 1, skip_solved: bool = True, brain_client=None):
        self._semaphore = asyncio.Semaphore(concurrency)
        self._skip_solved = skip_solved
        self._brain = brain_client

    async def prepare(self, tasks: List[dict]) -> List[PuzzleTask]:
        """Order tasks by estimated difficulty. Query KuzuDB for history."""
        puzzle_tasks = []
        for task in tasks:
            difficulty = await self._estimate_difficulty(task)
            solved = await self._check_previously_solved(task["task_id"])
            puzzle_tasks.append(PuzzleTask(
                task_id=task["task_id"],
                estimated_difficulty=difficulty,
                previously_solved=solved,
                grid_size=task.get("grid_size", 0),
                archetype_hint=task.get("archetype", None),
            ))

        # Sort: easy first, skip solved
        if self._skip_solved:
            puzzle_tasks = [t for t in puzzle_tasks if not t.previously_solved]
        puzzle_tasks.sort(key=lambda t: t.estimated_difficulty)
        return puzzle_tasks

    async def run_batch(self, tasks: List[PuzzleTask], run_fn) -> List[dict]:
        """Run tasks with concurrency control and health checks."""
        results = []
        total = len(tasks)
        start_time = time.time()

        for i, task in enumerate(tasks):
            # Health check
            if not await self._health_check():
                logger.error("Health check failed, pausing...")
                await asyncio.sleep(10)
                if not await self._health_check():
                    logger.error("Health check failed again, stopping batch")
                    break

            async with self._semaphore:
                result = await run_fn(task)
                results.append(result)

            # ETA
            elapsed = time.time() - start_time
            avg_per_puzzle = elapsed / (i + 1)
            remaining = (total - i - 1) * avg_per_puzzle
            logger.info("Progress: %d/%d, ETA: %.0fs", i + 1, total, remaining)

        return results

    async def _estimate_difficulty(self, task) -> float:
        """Estimate difficulty from historical data or heuristics."""
        # Query KuzuDB for past attempts at this task
        # If never solved: 0.8 (hard)
        # If solved quickly: 0.2 (easy)
        # If solved after many steps: 0.5 (medium)
        # Fallback: grid_size based heuristic
        return 0.5  # Default

    async def _check_previously_solved(self, task_id) -> bool:
        # Query KuzuDB for successful solve
        return False

    async def _health_check(self) -> bool:
        # Ping LLM, check memory usage
        try:
            # Quick LLM ping
            return True
        except Exception:
            return False
```

### Step 2: Wire into runner.py

Replace for-loop:

```python
scheduler = PuzzleScheduler(
    concurrency=config.get("concurrency", 1),
    skip_solved=config.get("skip_solved", True),
    brain_client=brain_client,
)
ordered_tasks = await scheduler.prepare(tasks)
results = await scheduler.run_batch(ordered_tasks, self._run_puzzle)
```

### Step 3: Tests

Create `tests/test_b189_scheduler.py`:
1. Test easy puzzles ordered before hard ones
2. Test previously-solved puzzles skipped when configured
3. Test health check failure pauses batch
4. Test ETA calculation after first puzzle
5. Test concurrency=1 runs sequentially
6. Test concurrency=3 runs up to 3 at once

## Verification

```bash
pytest tests/test_b189_scheduler.py -v
pytest tests/test_arc3_durable_runner.py -v  # regression
```
