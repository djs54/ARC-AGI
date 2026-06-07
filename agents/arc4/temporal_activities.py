"""Temporal activity wrappers for ARC v2 phases."""

from __future__ import annotations

from temporalio import activity
from temporalio.exceptions import ApplicationError

from .types import (
    PerceptionSnapshot,
    ResolvedGoal,
    PlanningResult,
    VetDecision,
    ExecutionResult,
    EvaluationResult,
    WorkflowState,
    PhaseResult,
)


# Phase callables are injected at worker startup via a shared context object.
# This avoids import-time coupling and lets tests inject mocks.

_phase_registry: dict = {}


def register_phases(phases: dict) -> None:
    """Called by the worker at startup to inject phase callables."""
    _phase_registry.update(phases)


def _get_phase(name: str):
    if name not in _phase_registry:
        raise ApplicationError(f"Phase {name!r} not registered", non_retryable=True)
    return _phase_registry[name]


@activity.defn
async def perceive_activity(input: dict) -> dict:
    state = WorkflowState.from_dict(input["state"])
    observation = input["observation"]
    phase = _get_phase("perceive")
    result = phase(state, observation)
    return {"result": result.to_dict(), "state": state.to_dict()}


@activity.defn
async def resolve_activity(input: dict) -> dict:
    state = WorkflowState.from_dict(input["state"])
    perception = PerceptionSnapshot.from_dict(input["perception"])
    phase = _get_phase("resolve")
    result = phase(state, perception)
    return {"result": result.to_dict(), "state": state.to_dict()}


@activity.defn
async def plan_activity(input: dict) -> dict:
    state = WorkflowState.from_dict(input["state"])
    perception = PerceptionSnapshot.from_dict(input["perception"])
    goal = ResolvedGoal.from_dict(input["goal"])
    phase = _get_phase("plan")
    result = phase(state, perception, goal)
    return {"result": result.to_dict(), "state": state.to_dict()}


@activity.defn
async def vet_activity(input: dict) -> dict:
    state = WorkflowState.from_dict(input["state"])
    perception = PerceptionSnapshot.from_dict(input["perception"])
    goal = ResolvedGoal.from_dict(input["goal"])
    plan = PlanningResult.from_dict(input["plan"])
    phase = _get_phase("vet")
    result = phase(state, perception, goal, plan)
    return {"result": result.to_dict(), "state": state.to_dict()}


@activity.defn
async def execute_activity(input: dict) -> dict:
    state = WorkflowState.from_dict(input["state"])
    perception = PerceptionSnapshot.from_dict(input["perception"])
    goal = ResolvedGoal.from_dict(input["goal"])
    vet = VetDecision.from_dict(input["vet"])
    phase = _get_phase("execute")
    result = phase(state, perception, goal, vet)
    return {"result": result.to_dict(), "state": state.to_dict()}


@activity.defn
async def evaluate_activity(input: dict) -> dict:
    state = WorkflowState.from_dict(input["state"])
    perception = PerceptionSnapshot.from_dict(input["perception"])
    goal = ResolvedGoal.from_dict(input["goal"])
    execution = ExecutionResult.from_dict(input["execution"])
    phase = _get_phase("evaluate")
    result = phase(state, perception, goal, execution)
    return {"result": result.to_dict(), "state": state.to_dict()}
