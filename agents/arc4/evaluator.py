"""Deterministic ARC v2 post-action evaluation."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Mapping

from agents.common.failure_taxonomy import FailureTaxonomy, classify_failure
from .compliance_checks import check_shift_b_invariants
from .ports import GraphQueryPort
from .types import EvaluationResult, ExecutionResult, PhaseResult, PhaseStatus, ResolvedGoal, WorkflowDecision, WorkflowPhase, WorkflowState

# A150: predicted-outcome kinds that are too weak to count as a confirming
# prediction on their own — e.g. "grid_change" matches nearly any action in a
# click-based game, so treating it as confirmed-without-progress means
# falsification never accumulates for the most common candidate shape.
WEAK_PREDICTION_KINDS = frozenset({"grid_change"})


@dataclass(slots=True)
class EvaluationLimits:
    repeated_falsification_threshold: int = 2
    exhausted_action_attempt_threshold: int = 4
    stale_repeat_threshold: int = 2  # override progress after N prior attempts of the same action
    causal_override_confidence_threshold: float = 0.3  # A163: below this, a "supported" causal path is too weak to trust


class Evaluator:
    """Compare prediction and outcome, then return the next workflow decision."""

    def __init__(self, graph_query_port: GraphQueryPort | None = None, *, limits: EvaluationLimits | None = None) -> None:
        self._graph_query_port = graph_query_port
        self._limits = limits or EvaluationLimits()
        # A244: scratch flag, reset at the top of every evaluate() call and
        # set True by evaluate()'s own fetch_causal_path except branch or
        # _action_space_exhausted's fetch_untested_actions except branch.
        # Read back into EvaluationResult.degraded when the result is built.
        # Same instance-scratch design as PlanGenerator._degraded/
        # PlanVetter._degraded (A237) -- confirmed via arc_runtime/bundle.py
        # that Evaluator is likewise a single long-lived instance
        # (`Evaluator(graph_query_port=graph_port).evaluate` bound once and
        # reused for every cycle of an episode), never called re-entrantly/
        # concurrently on itself, so a reset-per-evaluate() scratch
        # attribute is safe for a purely additive visibility signal.
        self._degraded = False

    def __call__(
        self,
        state: WorkflowState,
        perception: Any,
        goal: ResolvedGoal,
        execution: ExecutionResult,
    ) -> PhaseResult[EvaluationResult]:
        return self.evaluate(state, perception, goal, execution)

    def evaluate(
        self,
        state: WorkflowState,
        perception: Any,
        goal: ResolvedGoal,
        execution: ExecutionResult,
    ) -> PhaseResult[EvaluationResult]:
        # A244: reset before this cycle's graph_port calls so a prior
        # cycle's degradation never leaks forward into this one.
        self._degraded = False
        action_family = self._action_family(execution.action_id)
        current_falsification_count = self._current_count(state, action_family, execution.action_id)
        current_attempt_count = self._current_attempt_count(state, action_family, execution.action_id)
        predicted_effect = self._normalize_text(execution.predicted_effect)
        actual_effect = self._normalize_text(execution.actual_effect)
        terminal_reason = self._terminal_reason(execution)
        action_space_exhausted, exhaustion_source = self._action_space_exhausted(execution, current_attempt_count)
        exec_meta = execution.metadata if isinstance(execution.metadata, Mapping) else {}
        level_gain = int(exec_meta.get("level_gain") or 0)
        meaningful_progress = bool(execution.did_progress)

        # A133: Override stale progress — repeated action with no new information
        # Use action-specific count (not family) so unrelated siblings don't trigger stale
        stale_override = False
        grid_unchanged = self._grid_unchanged(state, perception)
        grid_changed_flag_raw = exec_meta.get("grid_changed")
        if grid_changed_flag_raw is None:
            grid_changed_flag = not grid_unchanged
        else:
            grid_changed_flag = bool(grid_changed_flag_raw)
        observed_kind = (
            "level_gain"
            if level_gain > 0
            else "state_change"
            if str(exec_meta.get("state") or "") in ("WIN", "GAME_OVER")
            else "grid_change"
            if grid_changed_flag
            else "no_change"
        )

        predicted_outcome = {}
        if execution.candidate is not None and isinstance(getattr(execution.candidate, "predicted_outcome", None), Mapping):
            predicted_outcome = dict(execution.candidate.predicted_outcome)
        predicted_kind = predicted_outcome.get("kind") if predicted_outcome else None

        if predicted_kind:
            satisfies_map = {
                "grid_change": {"grid_change", "level_gain", "state_change"},
                "level_gain": {"level_gain"},
                "state_change": {"state_change"},
                "no_change": {"no_change"},
            }
            effect_match = observed_kind in satisfies_map.get(str(predicted_kind), set())
        else:
            # Legacy fallback for older tests/transports that only provide string predictions.
            effect_match = predicted_effect is not None and actual_effect is not None and predicted_effect == actual_effect
        action_specific_attempts = state.action_attempt_counts.get(execution.action_id, 0)
        if meaningful_progress and terminal_reason is None:
            if action_specific_attempts >= self._limits.stale_repeat_threshold:
                meaningful_progress = False
                stale_override = True
            elif grid_unchanged:
                meaningful_progress = False
                stale_override = True

        # A135: Graph causal path check — if the graph traces only contradictions, no real progress
        causal_path: dict[str, Any] = {}
        causal_override = False
        if meaningful_progress and terminal_reason is None and self._graph_query_port is not None:
            try:
                causal_path = self._graph_query_port.fetch_causal_path(execution.action_id)
                # A163: the server only ever returns an aggregate path_confidence, never
                # itemized supports/contradicts lists — a confidence-threshold check is
                # the closest honest substitute for the originally-designed itemized check.
                if causal_path.get("path_exists") and float(causal_path.get("path_confidence", 1.0) or 0.0) < self._limits.causal_override_confidence_threshold:
                    meaningful_progress = False
                    causal_override = True
            except Exception:
                # A244: graph unavailable — don't override (unchanged
                # fallback), degradation made visible instead of silently
                # absorbed.
                self._degraded = True

        # A150: "grid_change" is the weakest possible predicted-outcome kind — nearly
        # any action in a click-based game changes at least one pixel, so treating it
        # as confirmed-without-progress means falsification never accumulates for the
        # most common candidate shape. Force it into the falsification path instead.
        weak_prediction_override = False
        if predicted_kind in WEAK_PREDICTION_KINDS and effect_match and not meaningful_progress:
            effect_match = False
            weak_prediction_override = True

        decision = WorkflowDecision.CONTINUE
        falsification_delta = 0
        reason = ""

        if terminal_reason is not None:
            decision = WorkflowDecision.TERMINATE
            reason = terminal_reason
        elif action_space_exhausted:
            # A249: no longer TERMINATE here -- unlike terminal_reason (the
            # ARC API's own authoritative win/loss signal), this signal is
            # not environment-authoritative (see backlog/A249.md: "env_
            # reported" is dead code today, "graph_confirmed_no_untested"
            # and "threshold_only" are both decided by this evaluator, not
            # the environment). It previously shared terminal_reason's
            # workflow.py short-circuit and ended the whole episode before
            # Annatar -- the codebase's single designated decision-owner --
            # ever got a say. decision stays CONTINUE (the default set
            # above); workflow.py now threads this metadata flag to Annatar
            # via the same channel stall_reason already uses.
            # action_space_exhausted/exhaustion_source remain in metadata
            # unchanged below, so nothing downstream loses visibility.
            reason = "action_space_exhausted"
        elif meaningful_progress:
            reason = "meaningful_progress"
        elif effect_match:
            reason = "prediction_confirmed_without_progress"
        else:
            if grid_changed_flag and not weak_prediction_override:
                falsification_delta = 0
                reason = "effect_without_progress"
            elif weak_prediction_override:
                falsification_delta = 1
                reason = "weak_prediction_falsified"
                projected_count = current_falsification_count + falsification_delta
                if projected_count >= self._limits.repeated_falsification_threshold:
                    decision = WorkflowDecision.PIVOT
                    reason = "repeated_falsification"
            else:
                falsification_delta = 1
                projected_count = current_falsification_count + falsification_delta
                if projected_count >= self._limits.repeated_falsification_threshold:
                    decision = WorkflowDecision.PIVOT
                    reason = "repeated_falsification"
                else:
                    reason = "prediction_falsified"

        if level_gain > 0:
            progress_tier = "level"
        elif not meaningful_progress and grid_changed_flag:
            progress_tier = "grid_change"
        else:
            progress_tier = "flat"

        compliance_violations = check_shift_b_invariants(execution)

        metadata_dict = {
            "action_id": execution.action_id,
            "action_family": action_family,
            "predicted_effect": execution.predicted_effect,
            "actual_effect": execution.actual_effect,
            "effect_match": effect_match,
            "current_falsification_count": current_falsification_count,
            "projected_falsification_count": current_falsification_count + falsification_delta,
            "current_attempt_count": current_attempt_count,
            "meaningful_progress": meaningful_progress,
            "terminal_reason": terminal_reason,
            "action_space_exhausted": action_space_exhausted,
            "falsification_pressure_after": 0 if meaningful_progress else current_falsification_count + falsification_delta,
            "goal_id": goal.selected.goal_id,
            "stale_override": stale_override,
            "grid_unchanged": grid_unchanged,
            "grid_changed": grid_changed_flag,
            "grid_diff": perception.metadata.get("grid_diff", {}) if isinstance(getattr(perception, "metadata", None), Mapping) else {},
            "level_gain": level_gain,
            "progress_tier": progress_tier,
            "predicted_kind": predicted_kind,
            "observed_kind": observed_kind,
            "causal_override": causal_override,
            "causal_path": causal_path,
            "weak_prediction_override": weak_prediction_override,
            "compliance_violations": compliance_violations,
        }
        if action_space_exhausted:
            metadata_dict["exhaustion_source"] = exhaustion_source

        evaluation = EvaluationResult(
            decision=decision,
            meaningful_progress=meaningful_progress,
            falsification_delta=falsification_delta,
            reason=reason,
            degraded=self._degraded,
            metadata=metadata_dict,
        )

        evaluation.metadata["graph_recording"] = self._record_evaluation(evaluation)
        evaluation.metadata["transition_recording"] = self._record_transition(perception, execution, state)
        evaluation.metadata["rule_recording"] = self._record_rule_evidence(perception, execution, state)
        # A255: evaluation.degraded was snapshotted from self._degraded above,
        # before these three write-path calls ran -- any of the three except
        # branches above (or an earlier read-path one) may have set
        # self._degraded = True since that snapshot. Re-sync so the returned
        # EvaluationResult actually reflects write-path degradation instead
        # of silently dropping it (the write-path self._degraded assignments
        # would otherwise be dead writes as far as this method's caller can
        # observe).
        evaluation.degraded = self._degraded
        return PhaseResult(phase=WorkflowPhase.EVALUATE, status=PhaseStatus.TERMINATE if decision == WorkflowDecision.TERMINATE else PhaseStatus.OK, payload=evaluation, reason=reason or None)

    def _record_transition(self, perception: Any, execution: ExecutionResult, state: WorkflowState) -> str:
        """A176: persist A170's grid_diff as a graph State Node."""
        if self._graph_query_port is None:
            return "skipped"

        record = getattr(self._graph_query_port, "record_transition", None)
        if record is None:
            return "skipped"

        grid_diff = self._grid_diff(perception)
        if not grid_diff:
            return "skipped"

        try:
            result = record(execution, grid_diff, getattr(perception, "entities", ()))
        except Exception:
            self._degraded = True  # A255: mirrors fetch_causal_path's A244 pattern -- was silently swallowed
            return "failed"
        # A183: only a confirmed real write counts toward world_model_node_writes.
        if isinstance(result, Mapping) and result.get("status") == "ok":
            state.world_model_node_writes += 1
        return "ok"

    def _record_rule_evidence(self, perception: Any, execution: ExecutionResult, state: WorkflowState) -> str:
        """A177: extract and send candidate rule signatures for this step's
        observed transitions. A186: also pass perception.entities so the
        port can derive precondition features for cross-game mechanic
        fusion, when the port's record_rule_evidence accepts the extra
        parameter -- pre-A186 graph ports (including test doubles) that only
        take (execution, grid_diff) keep working unchanged."""
        if self._graph_query_port is None:
            return "skipped"

        record = getattr(self._graph_query_port, "record_rule_evidence", None)
        if record is None:
            return "skipped"

        grid_diff = self._grid_diff(perception)
        if not grid_diff:
            return "skipped"

        try:
            if self._accepts_entities_param(record):
                result = record(execution, grid_diff, getattr(perception, "entities", ()))
            else:
                result = record(execution, grid_diff)
        except Exception:
            self._degraded = True  # A255
            return "failed"
        # A183: a real rule create/confirm/falsify writes PREDICTS/
        # CONFIRMED_BY/FALSIFIED_BY edges -- only count a confirmed write.
        if isinstance(result, Mapping) and result.get("status") == "ok":
            state.world_model_edge_writes += 1
        return "ok"

    @staticmethod
    def _accepts_entities_param(record: Any) -> bool:
        try:
            parameters = inspect.signature(record).parameters
        except (TypeError, ValueError):
            return False
        if any(parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters.values()):
            return True
        return len(parameters) >= 3

    @staticmethod
    def _grid_diff(perception: Any) -> Mapping[str, Any] | None:
        metadata = getattr(perception, "metadata", None)
        if not isinstance(metadata, Mapping):
            return None
        return metadata.get("grid_diff") or None

    def _record_evaluation(self, evaluation: EvaluationResult) -> str:
        if self._graph_query_port is None:
            return "skipped"

        record = getattr(self._graph_query_port, "record_evaluation", None)
        if record is None:
            return "skipped"

        try:
            record(evaluation)
        except Exception:
            self._degraded = True  # A255
            return "failed"
        return "ok"

    @staticmethod
    def _grid_unchanged(state: WorkflowState, perception: Any) -> bool:
        """Check if the grid is unchanged using the perception's own repeat/loop signals."""
        repeated = getattr(perception, "repeated_grid_count", 0)
        loop = getattr(perception, "loop_signal", False)
        return bool(repeated > 0 or loop)

    @staticmethod
    def _action_family(action_id: str) -> str:
        for separator in ("@", ":", "-", "_"):
            if separator in action_id:
                return action_id.split(separator, 1)[0]
        return action_id

    @staticmethod
    def _normalize_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        return normalized or None

    @staticmethod
    def _current_count(state: WorkflowState, action_family: str, action_id: str) -> int:
        if action_family in state.action_falsification_counts:
            return state.action_falsification_counts[action_family]
        return state.action_falsification_counts.get(action_id, 0)

    @staticmethod
    def _current_attempt_count(state: WorkflowState, action_family: str, action_id: str) -> int:
        if action_family in state.action_attempt_counts:
            return state.action_attempt_counts[action_family]
        return state.action_attempt_counts.get(action_id, 0)

    @staticmethod
    def _terminal_reason(execution: ExecutionResult) -> str | None:
        metadata = execution.metadata if isinstance(execution.metadata, Mapping) else {}
        for key in ("terminal", "terminated", "done", "game_over", "solved", "victory"):
            if bool(metadata.get(key)):
                return key
        observation = execution.observation if isinstance(execution.observation, Mapping) else {}
        for key in ("terminal", "terminated", "done", "game_over", "solved", "victory"):
            if bool(observation.get(key)):
                return key
        return None

    def _action_space_exhausted(self, execution: ExecutionResult, current_attempt_count: int) -> tuple[bool, str]:
        metadata = execution.metadata if isinstance(execution.metadata, Mapping) else {}
        if bool(metadata.get("action_space_exhausted")) or bool(metadata.get("exhausted_action_space")):
            return True, "env_reported"
        if current_attempt_count < self._limits.exhausted_action_attempt_threshold:
            return False, ""
        if self._graph_query_port is not None:
            fetch_untested = getattr(self._graph_query_port, "fetch_untested_actions", None)
            if fetch_untested is not None:
                try:
                    untested = fetch_untested()
                    if untested:
                        return False, ""
                    return True, "graph_confirmed_no_untested"
                except Exception:
                    # A244: falls through to "threshold_only" below
                    # (unchanged fallback) -- but that value is identical to
                    # the "no graph configured at all" case, so a real graph
                    # outage here would otherwise be indistinguishable in
                    # the trace. Make it visible instead.
                    self._degraded = True
        return True, "threshold_only"


def classify_v2_termination(status: str, reason: str, exception: BaseException | None = None) -> str:
    """Map v2 WorkflowStatus to v1 FailureTaxonomy values.

    A180: the dict below must cover every (status, reason) pair
    agents/arc4/workflow.py's self._finish(...) call sites can actually
    produce -- WorkflowStatus.SKIPPED ("second_veto") and
    WorkflowStatus.TERMINATED were previously absent entirely, so both
    silently fell through to the CRASH default even though neither is an
    actual crash (confirmed live: a clean run with no exception anywhere
    in its logs was labeled failure_class=crash). "completed"/"won" never
    matched anything real -- evaluator.py's _terminal_reason surfaces
    "solved"/"victory" (among others) as the TERMINATED reason, not those
    two -- replaced with the real success-reason strings.
    """
    if exception is not None:
        return classify_failure(exception).value

    mapping = {
        # Success -- these are wins, not failures, and must carry no
        # FailureTaxonomy value at all.
        "solved": None,
        "victory": None,
        # Reasoning/strategy failures -- the process worked as designed,
        # it just didn't find a way to solve the puzzle.
        "stalled": FailureTaxonomy.STRATEGY_EXHAUSTED.value,
        "stall_detected": FailureTaxonomy.STRATEGY_EXHAUSTED.value,
        "second_veto": FailureTaxonomy.STRATEGY_EXHAUSTED.value,
        # WorkflowStatus.SKIPPED.value -- workflow.py only ever pairs this
        # status with reason "second_veto" today, but kept as an explicit
        # status-level fallback so a future SKIPPED reason doesn't silently
        # default to CRASH the same way second_veto used to.
        "skipped": FailureTaxonomy.STRATEGY_EXHAUSTED.value,
        "action_space_exhausted": FailureTaxonomy.STRATEGY_EXHAUSTED.value,
        # Generic/ambiguous terminal flags (evaluator.py::_terminal_reason
        # can surface any of these from observation/metadata, and
        # workflow.py falls back to the literal "terminated" when the
        # evaluator's reason is empty) -- not proven wins, but not crashes
        # either; default them to the same reasoning-failure bucket rather
        # than the infrastructure-failure one.
        "terminal": FailureTaxonomy.STRATEGY_EXHAUSTED.value,
        "terminated": FailureTaxonomy.STRATEGY_EXHAUSTED.value,
        "done": FailureTaxonomy.STRATEGY_EXHAUSTED.value,
        "game_over": FailureTaxonomy.STRATEGY_EXHAUSTED.value,
        "budget_exhausted": FailureTaxonomy.WALL_CLOCK_TIMEOUT.value,
        # Actual crashes -- WorkflowStatus.CRASHED is explicit here rather
        # than relying on it falling through to the default.
        "crashed": FailureTaxonomy.CRASH.value,
        "crash": FailureTaxonomy.CRASH.value,
        "crash_in_perceive": FailureTaxonomy.CRASH.value,
        "crash_in_resolve": FailureTaxonomy.CRASH.value,
        "crash_in_plan": FailureTaxonomy.CRASH.value,
        "crash_in_vet": FailureTaxonomy.CRASH.value,
        "crash_in_execute": FailureTaxonomy.CRASH.value,
        "crash_in_evaluate": FailureTaxonomy.CRASH.value,
    }
    return mapping.get(reason, mapping.get(status, FailureTaxonomy.CRASH.value))