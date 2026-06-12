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
                return self._finish("budget_exhausted", "budget_exhausted")

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
                    return self._finish("skipped", "second_veto")

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

            # Update state counters (mirror WorkflowOrchestrator._record_*)
            action_id = execution.get("action_id", "")
            attempt_counts = self._state.setdefault("action_attempt_counts", {})
            attempt_counts[action_id] = attempt_counts.get(action_id, 0) + 1

            if evaluation.get("meaningful_progress"):
                self._state["consecutive_no_progress_count"] = 0
            else:
                self._state["consecutive_no_progress_count"] = self._state.get("consecutive_no_progress_count", 0) + 1
                falsification_counts = self._state.setdefault("action_falsification_counts", {})
                delta = max(0, evaluation.get("falsification_delta", 0))
                falsification_counts[action_id] = falsification_counts.get(action_id, 0) + delta

            self._state["step_index"] = step + 1

            # Stall check — only stall after exhausting all actions
            # AND completing multiple passes (actions may behave differently
            # as game state evolves across cycles).
            no_progress = self._state.get("consecutive_no_progress_count", 0)
            if no_progress >= max_no_progress:
                available = observation.get("available_actions", [])
                num_available = len(available) or 1
                tested = len(self._state.get("action_attempt_counts", {}))
                untested_remaining = num_available - tested
                if untested_remaining > 0:
                    pass  # still have untested actions — keep exploring
                elif no_progress >= num_available * 2:
                    # Tried every action at least twice with no progress
                    return self._finish("stalled", "stall_detected")

            # Terminate check (WorkflowDecision is a StrEnum with lowercase values)
            if str(evaluation.get("decision", "")).lower() == "terminate":
                return self._finish("terminated", evaluation.get("reason", "terminated"))

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
