import asyncio
import pytest

from agents.arc3.solver import (
    SolveEngine,
    PlanChunk,
    GameArchetype,
    VictoryCondition,
    VictoryType,
)


class _StubBrain:
    def __init__(self):
        self.register_plan_calls = []

    async def register_plan(self, *, goal, steps, session_id):
        self.register_plan_calls.append({
            "goal": goal, "steps": list(steps), "session_id": session_id,
        })
        return {"plan_id": f"plan-{len(self.register_plan_calls)}"}

    async def trace_event(self, **kwargs):
        return None


def _make_solver():
    solver = SolveEngine.__new__(SolveEngine)
    solver.brain = _StubBrain()
    solver.session_id = "test-session"
    solver._archetype = GameArchetype.RACE
    solver._victory_condition = VictoryCondition(
        condition_type=VictoryType.REACH_GOAL,
        description="reach the goal",
        confidence=0.8,
    )
    solver._last_registered_top_plan = None
    solver._last_registered_chunk_plan = None
    solver._last_registered_top_fingerprint = None
    solver._last_registered_chunk_fingerprint = None
    solver._last_registered_chunk_plan_id = None
    solver._solve_plan_id = None
    solver._emit_trace = None
    return solver


def test_chunk_dedup_ignores_cosmetic_description_change():
    """Two chunks that share archetype, vc, and steps but differ only in
    chunk-description wording must produce exactly ONE register_plan call."""
    solver = _make_solver()
    chunk_a = PlanChunk(
        description="Plateau Exploitation: commit to ACTION2",
        estimated_actions=["ACTION2", "ACTION2", "ACTION2"],
    )
    chunk_b = PlanChunk(
        description="Plateau Exploitation: commit to ACTION2 (step 5)",
        estimated_actions=["ACTION2", "ACTION2", "ACTION2"],
    )
    asyncio.run(solver._register_chunk_plan(chunk_a, step=0))
    asyncio.run(solver._register_chunk_plan(chunk_b, step=1))
    assert len(solver.brain.register_plan_calls) == 1
    assert chunk_a.plan_id == chunk_b.plan_id


def test_chunk_dedup_respects_archetype_change():
    """Two chunks identical except for archetype must produce TWO register_plan calls."""
    solver = _make_solver()
    chunk = PlanChunk(
        description="Explore",
        estimated_actions=["ACTION1"],
    )
    asyncio.run(solver._register_chunk_plan(chunk, step=0))
    solver._archetype = GameArchetype.CHASE  # archetype flip
    asyncio.run(solver._register_chunk_plan(chunk, step=1))
    assert len(solver.brain.register_plan_calls) == 2


def test_chunk_dedup_respects_step_list_change():
    """Two chunks identical except for estimated_actions must produce TWO calls."""
    solver = _make_solver()
    chunk_a = PlanChunk(description="Explore", estimated_actions=["ACTION1"])
    chunk_b = PlanChunk(description="Explore", estimated_actions=["ACTION2"])
    asyncio.run(solver._register_chunk_plan(chunk_a, step=0))
    asyncio.run(solver._register_chunk_plan(chunk_b, step=1))
    assert len(solver.brain.register_plan_calls) == 2
