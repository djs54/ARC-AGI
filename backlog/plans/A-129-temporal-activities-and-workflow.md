# A-129 — Convert ARC v2 Phases to Temporal Activities and Workflow

Card: backlog/A129.md

## Summary

Create the Temporal Activity wrappers for each ARC v2 phase and the `ArcPuzzleWorkflow` definition that replaces the in-process `WorkflowOrchestrator.run()` loop. This is the core conversion — after this card, the ARC agent can run durably through Temporal.

## Implementation Approach

### 1. Serialization on Types (`agents/arc4/types.py`)

Temporal activities communicate via JSON-serializable dicts. Add `to_dict()` and `@classmethod from_dict(cls, d)` to these dataclasses:

- `WorkflowState`
- `PerceptionSnapshot`
- `ResolvedGoal`
- `PlanningResult`
- `VetDecision`
- `ExecutionResult`
- `EvaluationResult`
- `WorkflowRunResult`
- `PhaseResult`

Pattern for each:

```python
def to_dict(self) -> dict:
    """Serialize for Temporal activity transport."""
    return {field: getattr(self, field) for field in self.__dataclass_fields__}

@classmethod
def from_dict(cls, d: dict) -> "ClassName":
    """Deserialize from Temporal activity transport."""
    return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
```

For enum fields, serialize as `.value` and deserialize with `EnumClass(value)`.
For nested dataclasses, call `.to_dict()` / `.from_dict()` recursively.

### 2. Temporal Activities (`agents/arc4/temporal_activities.py`)

Each activity wraps one phase callable. Activities are plain async functions decorated with `@activity.defn`. They receive a dict, deserialize it, call the phase, and return a serialized dict.

```python
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
```

**Error model** (matching Sandbox `temporal_activities.py`):
- Transient failures (MCP disconnect, LLM timeout, network) — raise standard exceptions → Temporal retries (default: 3 attempts, exponential backoff)
- Permanent failures (missing phase, invalid input shape) — raise `ApplicationError(non_retryable=True)` → workflow fails immediately
- `start_to_close_timeout = timedelta(seconds=300)` per activity (5 min, matching Sandbox)

### 3. Temporal Workflow (`agents/arc4/temporal_workflows.py`)

```python
"""Temporal workflow definition for ARC v2 puzzle solving."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .types import WorkflowPhase, WorkflowStatus, PhaseStatus, WorkflowDecision


ACTIVITY_TIMEOUT = timedelta(seconds=300)
RETRY_POLICY = RetryPolicy(maximum_attempts=3, backoff_coefficient=2.0)


@workflow.defn
class ArcPuzzleWorkflow:
    """Durable ARC v2 puzzle-solving workflow."""

    def __init__(self) -> None:
        self._state: dict = {}
        self._phase_results: list[dict] = []
        self._status: str = "running"

    @workflow.run
    async def run(self, input: dict) -> dict:
        self._state = input["state"]
        observation = input["observation"]
        max_cycles = input.get("max_cycles", 10)
        max_replan = input.get("max_replan_passes_per_cycle", 1)
        max_no_progress = input.get("max_consecutive_no_progress", 4)

        while True:
            step = self._state.get("step_index", 0)
            if step >= max_cycles:
                return self._finish("BUDGET_EXHAUSTED", "budget_exhausted")

            # Phase 1: Perceive
            perceive_out = await workflow.execute_activity(
                "perceive_activity",
                {"state": self._state, "observation": observation},
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=RETRY_POLICY,
            )
            self._state = perceive_out["state"]
            perception = perceive_out["result"]["payload"]
            self._phase_results.append(perceive_out["result"])

            # Phase 2: Resolve goal
            resolve_out = await workflow.execute_activity(
                "resolve_activity",
                {"state": self._state, "perception": perception},
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=RETRY_POLICY,
            )
            self._state = resolve_out["state"]
            goal = resolve_out["result"]["payload"]
            self._phase_results.append(resolve_out["result"])

            # Phase 3: Plan
            plan_out = await workflow.execute_activity(
                "plan_activity",
                {"state": self._state, "perception": perception, "goal": goal},
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=RETRY_POLICY,
            )
            self._state = plan_out["state"]
            plan = plan_out["result"]["payload"]
            self._phase_results.append(plan_out["result"])

            # Phase 4: Vet (Go/No-Go gate)
            vet_out = await workflow.execute_activity(
                "vet_activity",
                {"state": self._state, "perception": perception, "goal": goal, "plan": plan},
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=RETRY_POLICY,
            )
            self._state = vet_out["state"]
            vet = vet_out["result"]["payload"]
            self._phase_results.append(vet_out["result"])

            # Veto handling with replan
            if not vet.get("approved", True):
                # One replan attempt
                resolve_out2 = await workflow.execute_activity(
                    "resolve_activity",
                    {"state": self._state, "perception": perception},
                    start_to_close_timeout=ACTIVITY_TIMEOUT,
                    retry_policy=RETRY_POLICY,
                )
                self._state = resolve_out2["state"]
                goal = resolve_out2["result"]["payload"]

                plan_out2 = await workflow.execute_activity(
                    "plan_activity",
                    {"state": self._state, "perception": perception, "goal": goal},
                    start_to_close_timeout=ACTIVITY_TIMEOUT,
                    retry_policy=RETRY_POLICY,
                )
                self._state = plan_out2["state"]
                plan = plan_out2["result"]["payload"]

                vet_out2 = await workflow.execute_activity(
                    "vet_activity",
                    {"state": self._state, "perception": perception, "goal": goal, "plan": plan},
                    start_to_close_timeout=ACTIVITY_TIMEOUT,
                    retry_policy=RETRY_POLICY,
                )
                self._state = vet_out2["state"]
                vet = vet_out2["result"]["payload"]

                if not vet.get("approved", True):
                    return self._finish("SKIPPED", "second_veto")

            # Phase 5: Execute
            exec_out = await workflow.execute_activity(
                "execute_activity",
                {"state": self._state, "perception": perception, "goal": goal, "vet": vet},
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=RETRY_POLICY,
            )
            self._state = exec_out["state"]
            execution = exec_out["result"]["payload"]
            self._phase_results.append(exec_out["result"])

            # Phase 6: Evaluate
            eval_out = await workflow.execute_activity(
                "evaluate_activity",
                {"state": self._state, "perception": perception, "goal": goal, "execution": execution},
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=RETRY_POLICY,
            )
            self._state = eval_out["state"]
            evaluation = eval_out["result"]["payload"]
            self._phase_results.append(eval_out["result"])

            # Update state counters
            self._state["step_index"] = step + 1

            # Stall check
            no_progress = self._state.get("consecutive_no_progress_count", 0)
            if no_progress >= max_no_progress:
                available = observation.get("available_actions", [])
                tested = len(self._state.get("action_attempt_counts", {}))
                if len(available) - tested <= 0:
                    return self._finish("STALLED", "stall_detected")

            # Terminate check
            if evaluation.get("decision") == "TERMINATE":
                return self._finish("TERMINATED", evaluation.get("reason", "terminated"))

            # Next cycle uses execution's observation
            observation = execution.get("observation", observation)

    @workflow.query
    def get_state(self) -> dict:
        return {"state": self._state, "status": self._status, "completed_cycles": self._state.get("step_index", 0)}

    def _finish(self, status: str, reason: str) -> dict:
        self._status = status
        return {
            "status": status,
            "state": self._state,
            "phase_results": self._phase_results,
            "reason": reason,
            "completed_cycles": self._state.get("step_index", 0),
        }
```

### 4. Worker Phase Registration

Update `agents/arc4/temporal_worker.py` to call `register_phases()` before starting the worker, injecting the actual phase callables (constructed the same way `run_single_puzzle.py` builds `WorkflowDependencies`).

## Tests to Add

1. **`tests/test_a129_temporal_workflow.py`**
   - `test_workflow_produces_same_result_structure` — compare dict keys of Temporal workflow output vs `WorkflowOrchestrator.run()` output
   - `test_activity_timeout_configuration` — verify each activity has 300s timeout
   - `test_non_retryable_permanent_failure` — verify `ApplicationError(non_retryable=True)` propagates
   - `test_workflow_query_returns_state` — verify `get_state` query works mid-run
   - `test_serialization_round_trip` — for each type, `from_dict(x.to_dict()) == x`

## Validation Commands

```bash
make test-a
pytest tests/test_a129_temporal_workflow.py -v
```

## Assumptions

- Phase callables are injected at worker startup, not imported at workflow definition time (Temporal sandboxing requirement)
- Activity input/output is JSON-serializable dicts, not raw dataclasses
- The workflow mirrors `WorkflowOrchestrator.run()` logic exactly — no behavioral changes
- `workflow.unsafe.imports_passed_through()` is used for type-only imports in workflow module
