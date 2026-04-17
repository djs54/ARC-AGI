# Plan for B187 — Parallel Strategy Racing

## Card Metadata

- **Card ID**: B187
- **Priority**: P2
- **Dependencies**: B180 (budget), B183 (supervisor)

## Summary

Race 2-3 strategy variants per puzzle using `asyncio.gather()`. First-to-solve wins. Others cancelled. Each variant gets own orchestrator and 1/3 of budget.

## Current State

### Runner puzzle loop (runner.py:~53)

```python
for task in tasks:
    await self._run_puzzle(task, ...)
```

Single orchestrator per puzzle. No variant mechanism.

## Technical Approach

### Step 1: Define strategy variants

```python
@dataclass
class StrategyVariant:
    name: str
    config_overrides: dict  # Override default orchestrator config

VARIANTS = [
    StrategyVariant("normal", {}),
    StrategyVariant("explore_heavy", {"exploration_budget_multiplier": 2.0}),
    StrategyVariant("pattern_first", {"force_repl_verification": True, "early_execution_mode": True}),
]
```

### Step 2: Create agents/arc3/strategy_racer.py

```python
class StrategyRacer:
    def __init__(self, variants: List[StrategyVariant], budget_per_puzzle: float):
        self.variants = variants
        self.budget_per_variant = budget_per_puzzle / len(variants)

    async def race(
        self,
        task,
        brain_client,
        llm_client,
        adapter,
        **kwargs,
    ) -> dict:
        tasks = []
        for variant in self.variants:
            # Each variant gets own cost tracker with 1/3 budget
            cost_tracker = CostTracker(budget_usd=self.budget_per_variant, ...)
            # Each variant gets own orchestrator
            config = {**kwargs.get("config", {}), **variant.config_overrides}
            orchestrator = ARCOrchestrator(brain_client, llm_client, ..., config=config)

            task_coro = self._run_variant(variant.name, orchestrator, task, adapter, cost_tracker)
            tasks.append(asyncio.create_task(task_coro, name=variant.name))

        # Wait for first success or all failures
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        # Check if any succeeded
        winner = None
        for t in done:
            result = t.result()
            if result and result.get("correct"):
                winner = result
                break

        if not winner:
            # No success yet — wait for rest or all fail
            if pending:
                remaining_done, _ = await asyncio.wait(pending, return_when=asyncio.ALL_COMPLETED)
                for t in remaining_done:
                    result = t.result()
                    if result and result.get("correct"):
                        winner = result
                        break

        # Cancel remaining tasks
        for t in pending:
            t.cancel()

        # Return winner or best loser (highest judge score)
        if winner:
            return winner

        all_results = [t.result() for t in done if not t.cancelled()]
        return max(all_results, key=lambda r: r.get("judge_verdict", {}).get("composite_score", 0), default=all_results[0])

    async def _run_variant(self, name, orchestrator, task, adapter, cost_tracker):
        try:
            result = await self._execute_puzzle(orchestrator, task, adapter, cost_tracker)
            result["variant"] = name
            return result
        except asyncio.CancelledError:
            return {"variant": name, "cancelled": True}
        except Exception as exc:
            return {"variant": name, "error": str(exc)}
```

### Step 3: Session isolation

Each variant needs its own ARC API guid. Modify the runner to create separate adapter sessions:

```python
# In _run_variant:
guid = await adapter.start_session(task_id)  # Each variant gets own session
```

### Step 4: KuzuDB write serialization

Variants share the same KuzuDB instance. Use `asyncio.Lock` for writes:

```python
self._db_lock = asyncio.Lock()
# In brain_client wrapper:
async with self._db_lock:
    await db.execute_write(query, params)
```

### Step 5: Wire into runner.py

```python
if config.get("strategy_racing", False):
    racer = StrategyRacer(VARIANTS, budget_per_puzzle)
    result = await racer.race(task, brain_client, llm_client, adapter, config=config)
else:
    result = await self._run_puzzle(task, ...)  # Original path
```

### Step 6: Tests

Create `tests/test_b187_strategy_racer.py`:
1. Test 3 variants launched concurrently
2. Test first-to-solve cancels others
3. Test budget split: each variant gets 1/3
4. Test all variants fail → returns best loser by judge score
5. Test cancellation doesn't raise
6. Test configurable disable (single variant mode)
7. Test session isolation (different guids)

## Verification

```bash
pytest tests/test_b187_strategy_racer.py -v
# Integration test:
python run_single_puzzle.py --live-smoke --variants 2
```
