import asyncio
import time

import pytest

from agents.arc3.strategy_racer import race as strategy_race
from benchmarks.arc3.adapter import NoOpBrainClient


class DummyTask:
    def __init__(self, task_id="t1", game_id="g1"):
        self.task_id = task_id
        self.game_id = game_id


class DummyRunner:
    def __init__(self, base_budget=3.0):
        self.config = {"cost": {"budget_per_puzzle_usd": base_budget}, "strategy_racing_variants": ["A", "B", "C"]}
        self._raw_brain = NoOpBrainClient()
        self._ledger = []


@pytest.mark.asyncio
async def test_strategy_racer_picks_first_solver_and_splits_budget():
    runner = DummyRunner(base_budget=3.0)
    task = DummyTask()

    # variant_runner simulates different completion times and returns a simple result object
    async def variant_runner(variant_brain, session_id, task_obj, vcfg):
        # Validate budget splitting
        assert "cost" in vcfg
        assert pytest.approx(vcfg["cost"]["budget_per_puzzle_usd"], rel=1e-6) == 1.0

        # Derive variant id from session_id suffix
        var = session_id.split("-")[-2]
        # Simulate work: B finishes quickly and succeeds, others slower
        if var == "B":
            await asyncio.sleep(0.05)
            class R: pass
            r = R()
            r.correct = True
            return r, 0.05, None
        elif var == "A":
            await asyncio.sleep(0.15)
            class R: pass
            r = R()
            r.correct = False
            return r, 0.15, None
        else:
            # C slower but would also succeed if reached
            await asyncio.sleep(0.25)
            class R: pass
            r = R()
            r.correct = True
            return r, 0.25, None

    winner = await strategy_race(runner, task, variants=["A", "B", "C"], variant_runner=variant_runner)
    assert winner["variant"] == "B"
    assert winner["task_result"].correct is True
