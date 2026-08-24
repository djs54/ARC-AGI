"""Temporal workflow definition for ARC v2 puzzle solving."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .cycle_policy import check_budget, check_stall, count_base_actions, record_evaluation_outcome, termination_from_evaluation
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
            budget_reason = check_budget(step, max_cycles)
            if budget_reason is not None:
                return self._finish("budget_exhausted", budget_reason)

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
            candidate = execution.get("candidate") if isinstance(execution, dict) else None
            action_id = execution.get("action_id", "")
            action_key = (candidate.get("book_id") if isinstance(candidate, dict) else None) or action_id
            attempt_counts = self._state.setdefault("action_attempt_counts", {})
            attempt_counts[action_key] = attempt_counts.get(action_key, 0) + 1

            falsification_counts = self._state.setdefault("action_falsification_counts", {})
            self._state["consecutive_no_progress_count"] = record_evaluation_outcome(
                no_progress_count=int(self._state.get("consecutive_no_progress_count", 0) or 0),
                falsification_counts=falsification_counts,
                action_key=action_key,
                meaningful_progress=bool(evaluation.get("meaningful_progress")),
                falsification_delta=int(evaluation.get("falsification_delta", 0) or 0),
            )

            # Mirror WorkflowOrchestrator._record_evaluation_state's goal_failure_counts
            # bookkeeping (A152): reset to 0 on progress, increment otherwise, keyed by
            # the goal_id that was active for this cycle.
            goal_failure_counts = self._state.setdefault("goal_failure_counts", {})
            active_goal_selected = goal.get("selected") if isinstance(goal, dict) else None
            active_goal_id = active_goal_selected.get("goal_id") if isinstance(active_goal_selected, dict) else None
            if active_goal_id:
                if bool(evaluation.get("meaningful_progress")):
                    goal_failure_counts[active_goal_id] = 0
                else:
                    goal_failure_counts[active_goal_id] = goal_failure_counts.get(active_goal_id, 0) + 1

            self._state["step_index"] = step + 1

            no_progress = self._state.get("consecutive_no_progress_count", 0)
            available = observation.get("available_actions", [])
            # Count distinct base actions so ACTION6@x,y click targets don't
            # inflate the attempted count past the available action space.
            num_attempted = count_base_actions(self._state.get("action_attempt_counts", {}))
            stall_reason = check_stall(no_progress, max_no_progress, len(available), num_attempted)
            if stall_reason is not None:
                return self._finish("stalled", stall_reason)

            termination = termination_from_evaluation(evaluation.get("decision"), evaluation.get("reason"))
            if termination is not None:
                return self._finish(termination[0], termination[1])

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
