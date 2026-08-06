"""ARC v2 graph-query adapter over the landed B278 MCP tool surface."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .types import ExecutionResult, GoalHypothesis, PerceivedEntity, PerceptionSnapshot, PlanningResult, ResolvedGoal, VetDecision


ARC_V2_TOOL_NAMES = {
    "ingest_perception": "arc_perceive_state",
    "fetch_goal_evidence": "arc_get_goal_evidence",
    "fetch_game_context": "arc_get_game_context",
    "fetch_action_evidence": "arc_get_action_evidence",
    "fetch_untested_actions": "arc_get_untested_actions",
    "fetch_causal_path": "arc_get_causal_path",
    "record_action_effect": "arc_record_action_effect",
    "fetch_entity_movement": "arc_get_entity_movement",
    "classify_game_archetype": "arc_classify_game_archetype",
    "confirm_hypothesis": "arc_confirm_hypothesis",
    "contradict_hypothesis": "arc_contradict_hypothesis",
    "update_goal_confidence": "arc_update_goal_confidence",
    "fetch_mechanic_priors": "arc_get_mechanic_priors",
    "check_action_gate": "arc_check_action_gate",
    "record_reward_prediction_error": "arc_record_reward_prediction_error",
}


@dataclass(slots=True)
class ArcGraphQueryPort:
    """Sync adapter that maps ARC v2 graph calls to concrete B278 tool names."""

    brain_client: Any
    task_id: str
    session_id: str
    strict: bool = True
    tool_names: Mapping[str, str] = field(default_factory=lambda: dict(ARC_V2_TOOL_NAMES))

    def ingest_perception(self, perception: PerceptionSnapshot) -> dict[str, Any]:
        payload = {
            "task_id": self.task_id,
            "step": self._perception_step(perception),
            "grid_hash": perception.grid_hash,
            "entities": [self._serialize_entity(entity) for entity in perception.entities],
            "action_taken": str(perception.metadata.get("action_taken") or perception.metadata.get("selected_action") or ""),
            "effect": {
                "loop_signal": perception.loop_signal,
                "repeated_grid_count": perception.repeated_grid_count,
                "grid_shape": list(perception.grid_shape) if perception.grid_shape is not None else None,
            },
        }
        return self._normalize_write_result(self._call_tool("ingest_perception", payload), tool_key="ingest_perception")

    def fetch_goal_evidence(
        self,
        perception: PerceptionSnapshot,
        goal: ResolvedGoal | GoalHypothesis | None = None,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []

        records.extend(self._normalize_records(self._call_tool("fetch_goal_evidence", {"task_id": self.task_id}), source="goal_evidence"))
        records.extend(self._context_to_records(self._call_tool("fetch_game_context", {"task_id": self.task_id})))
        records.extend(
            self._normalize_records(
                self._call_tool(
                    "fetch_mechanic_priors",
                    {
                        "task_id": self.task_id,
                        "game_features": self._game_features(perception),
                        "action_patterns": self._action_patterns(goal),
                    },
                ),
                source="mechanic_priors",
            )
        )

        if self._infer_archetype(perception, goal):
            records.extend(
                self._normalize_records(
                    self._call_tool("classify_game_archetype", {"task_id": self.task_id, "grid_features": self._game_features(perception)}),
                    source="archetype",
                )
            )

        action_id = self._goal_action_id(goal)
        if action_id:
            records.extend(
                self._normalize_records(
                    self._call_tool("fetch_action_evidence", {"task_id": self.task_id, "action_id": action_id}),
                    source="action_evidence",
                )
            )

        return self._dedupe_records(records)

    # ── A135: Graph read tools for planner, vet, evaluator ──────────

    def fetch_untested_actions(self) -> list[str]:
        """Return action IDs the graph has never seen attempted for this task."""
        result = self._call_tool("fetch_untested_actions", {"task_id": self.task_id})
        return self._extract_action_ids(result)

    def fetch_per_action_evidence(self, action_id: str) -> dict[str, Any]:
        """Return accumulated evidence (supports, contradictions, confidence) for a specific action."""
        result = self._call_tool("fetch_action_evidence", {"task_id": self.task_id, "action_id": action_id})
        if isinstance(result, Mapping):
            if result.get("status") == "capability_missing":
                return {"supports": 0, "contradictions": 0, "confidence": 0.0, "attempts": 0}
            return {
                "supports": int(result.get("supports", result.get("support_count", 0)) or 0),
                "contradictions": int(result.get("contradictions", result.get("contradiction_count", result.get("falsified_count", 0))) or 0),
                "confidence": float(result.get("confidence", result.get("score", 0.0)) or 0.0),
                "attempts": int(result.get("attempts", result.get("attempt_count", result.get("evidence_count", result.get("steps_used", 0)))) or 0),
                "raw": dict(result),
            }
        return {"supports": 0, "contradictions": 0, "confidence": 0.0, "attempts": 0}

    def check_action_gate(self, action_id: str) -> dict[str, Any]:
        """Graph-backed go/no-go gate for an action based on accumulated evidence."""
        result = self._call_tool("check_action_gate", {"task_id": self.task_id, "action_id": action_id})
        if isinstance(result, Mapping):
            if result.get("status") == "capability_missing":
                return {"allowed": True, "reason": "no_gate_data"}
            return {
                "allowed": bool(result.get("allowed", result.get("approved", result.get("go", True)))),
                "reason": str(result.get("reason", result.get("explanation", "")) or ""),
                "evidence_summary": dict(result.get("evidence_summary", result.get("evidence", {})) or {}),
                "raw": dict(result),
            }
        return {"allowed": True, "reason": "no_gate_data"}

    def fetch_causal_path(self, action_id: str) -> dict[str, Any]:
        """Trace causal chain from action to hypothesis support/contradiction."""
        result = self._call_tool("fetch_causal_path", {"task_id": self.task_id, "action_id": action_id})
        if isinstance(result, Mapping):
            if result.get("status") == "capability_missing":
                return {"path_exists": False, "supports": [], "contradicts": []}
            supports = result.get("supports", result.get("supported_hypotheses", []))
            contradicts = result.get("contradicts", result.get("contradicted_hypotheses", []))
            return {
                "path_exists": bool(supports or contradicts or result.get("path_exists")),
                "supports": list(supports) if isinstance(supports, (list, tuple)) else [],
                "contradicts": list(contradicts) if isinstance(contradicts, (list, tuple)) else [],
                "path_confidence": float(result.get("path_confidence", 0.0) or 0.0),
                "raw": dict(result),
            }
        return {"path_exists": False, "supports": [], "contradicts": []}

    @staticmethod
    def _extract_action_ids(result: Any) -> list[str]:
        """Extract a list of action ID strings from a tool result."""
        if result is None:
            return []
        if isinstance(result, Mapping):
            if result.get("status") == "capability_missing":
                return []
            # Try common container keys
            for key in ("untested", "actions", "untested_actions", "action_ids", "results", "items"):
                value = result.get(key)
                if isinstance(value, (list, tuple)):
                    return [str(item.get("action_id") if isinstance(item, Mapping) else item) for item in value if item]
            # Single action_id
            if result.get("action_id"):
                return [str(result["action_id"])]
            return []
        if isinstance(result, (list, tuple)):
            return [str(item.get("action_id") if isinstance(item, Mapping) else item) for item in result if item]
        return []

    def record_plan(self, plan: PlanningResult) -> dict[str, Any]:
        payload = {
            "task_id": self.task_id,
            "hypothesis_id": self._goal_id_from_plan(plan),
            "evidence": {
                "candidate": self._serialize_candidate(getattr(plan, "candidate", None)),
                "alternatives": [self._serialize_candidate(candidate) for candidate in plan.alternatives],
                "needs_vet": plan.needs_vet,
                "metadata": dict(plan.metadata),
            },
        }
        return self._normalize_write_result(self._call_tool("confirm_hypothesis", payload), tool_key="confirm_hypothesis")

    def record_vet(self, vet: VetDecision) -> dict[str, Any]:
        candidate_id = self._vet_action_id(vet)
        if candidate_id is None:
            return {"status": "skipped", "tool": "record_vet"}

        tool_key = "confirm_hypothesis" if vet.approved else "contradict_hypothesis"
        payload = {
            "task_id": self.task_id,
            "hypothesis_id": candidate_id,
            "evidence": {"reason": vet.reason, "approved": vet.approved, "metadata": dict(vet.metadata)},
        }
        return self._normalize_write_result(self._call_tool(tool_key, payload), tool_key=tool_key)

    def record_execution(self, execution: ExecutionResult) -> dict[str, Any]:
        payload = {
            "task_id": self.task_id,
            "action_id": execution.action_id,
            "step": self._execution_step(execution),
            "effect": {
                "predicted_effect": execution.predicted_effect,
                "actual_effect": execution.actual_effect,
                "did_progress": execution.did_progress,
                "observation_state": self._observation_state(execution.observation),
            },
            "entities_affected": list(self._entities_affected(execution.observation)),
        }
        return self._normalize_write_result(self._call_tool("record_action_effect", payload), tool_key="record_action_effect")

    def record_evaluation(self, evaluation: Any) -> dict[str, Any]:
        metadata = evaluation.metadata if isinstance(getattr(evaluation, "metadata", None), Mapping) else {}
        goal_id = str(metadata.get("goal_id") or metadata.get("resolved_goal_id") or "")
        action_id = str(metadata.get("action_id") or metadata.get("candidate_action_id") or "")
        updates: list[dict[str, Any]] = []

        if goal_id:
            confidence = float(metadata.get("goal_confidence", 0.0) or 0.0)
            if evaluation.meaningful_progress:
                confidence = max(confidence, 0.75)
            elif confidence <= 0.0:
                confidence = 0.2
            updates.append(
                self._normalize_write_result(
                    self._call_tool(
                        "update_goal_confidence",
                        {
                            "task_id": self.task_id,
                            "goal_id": goal_id,
                            "new_confidence": confidence,
                            "has_meaningful_progress": bool(evaluation.meaningful_progress),
                        },
                    ),
                    tool_key="update_goal_confidence",
                )
            )

        if action_id:
            updates.append(
                self._normalize_write_result(
                    self._call_tool(
                        "record_action_effect",
                        {
                            "task_id": self.task_id,
                            "action_id": action_id,
                            "step": metadata.get("step") or metadata.get("execution_step") or 0,
                            "effect": {
                                "effect_match": bool(metadata.get("effect_match")),
                                "predicted_kind": metadata.get("predicted_kind"),
                                "observed_kind": metadata.get("observed_kind"),
                                "effect_kind": metadata.get("observed_kind"),
                                "did_progress": bool(evaluation.meaningful_progress),
                                "falsification_delta": int(getattr(evaluation, "falsification_delta", 0) or 0),
                            },
                        },
                    ),
                    tool_key="record_action_effect",
                )
            )
            updates.append(
                self._normalize_write_result(
                    self._call_tool(
                        "record_reward_prediction_error",
                        {
                            "task_id": self.task_id,
                            "action_id": action_id,
                            "step": metadata.get("step") or metadata.get("execution_step") or 0,
                            # B278's arc_record_reward_prediction_error derives the
                            # error itself as (actual_reward - predicted_reward) and
                            # bumps falsified_count when it is < -0.3. The planner
                            # proposed this action expecting a productive effect
                            # (predicted 1.0); a no-progress step realises 0.0, giving
                            # error -1.0 → falsification. Sending the legacy
                            # "reward_prediction_error" key is silently ignored.
                            "predicted_reward": 1.0,
                            "actual_reward": 1.0 if evaluation.meaningful_progress else 0.0,
                        },
                    ),
                    tool_key="record_reward_prediction_error",
                )
            )

        if not updates:
            return {"status": "skipped", "tool": "record_evaluation"}
        return {"status": "ok", "tool": "record_evaluation", "updates": updates}

    def _call_tool(self, tool_key: str, payload: Mapping[str, Any]) -> Any:
        tool_name = self.tool_names[tool_key]

        try:
            if hasattr(self.brain_client, "call_tool"):
                result = self.brain_client.call_tool(tool_name, dict(payload))
            else:
                method = getattr(self.brain_client, tool_name, None)
                if method is None:
                    raise AttributeError(f"brain client does not expose {tool_name}")
                result = method(**dict(payload))
        except Exception as exc:
            if self._is_missing_tool_error(exc, tool_name):
                if self.strict:
                    raise RuntimeError(f"required ARC tool missing: {tool_name}") from exc
                return {"status": "capability_missing", "tool": tool_name, "error": str(exc)}
            raise

        if inspect.isawaitable(result):
            try:
                # Check if we're inside an existing event loop (e.g. Temporal worker)
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None

                if loop is not None and loop.is_running():
                    # Inside a running loop (Temporal activity) — use nest_asyncio or thread
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        return pool.submit(asyncio.run, result).result()
                else:
                    return asyncio.run(result)
            except Exception as exc:
                if self._is_missing_tool_error(exc, tool_name):
                    if self.strict:
                        raise RuntimeError(f"required ARC tool missing: {tool_name}") from exc
                    return {"status": "capability_missing", "tool": tool_name, "error": str(exc)}
                raise
        return result

    @staticmethod
    def _is_missing_tool_error(exc: Exception, tool_name: str) -> bool:
        message = str(exc)
        return tool_name in message and any(fragment in message for fragment in ("Unknown method", "missing", "not expose", "not found"))

    def _normalize_write_result(self, result: Any, *, tool_key: str) -> dict[str, Any]:
        if isinstance(result, Mapping):
            payload = dict(result)
            payload.setdefault("tool", self.tool_names[tool_key])
            payload.setdefault("status", "ok" if not payload.get("error") else "error")
            if payload.get("status") == "capability_missing" and self.strict:
                raise RuntimeError(f"required ARC tool missing: {self.tool_names[tool_key]}")
            return payload
        return {"tool": self.tool_names[tool_key], "status": "ok", "result": result}

    def _normalize_records(self, result: Any, *, source: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        sequence = self._extract_sequence(result)
        if sequence:
            for item in sequence:
                if isinstance(item, Mapping):
                    record = self._record_from_mapping(item, source=source)
                    if record is not None:
                        records.append(record)
        elif isinstance(result, Mapping):
            record = self._record_from_mapping(result, source=source)
            if record is not None:
                records.append(record)
        return records

    def _record_from_mapping(self, item: Mapping[str, Any], *, source: str) -> dict[str, Any] | None:
        if not item:
            return None
        if item.get("status") == "capability_missing":
            return None
        goal_id = str(
            item.get("goal_id")
            or item.get("hypothesis_id")
            or item.get("id")
            or item.get("key")
            or item.get("lesson_id")
            or item.get("mechanic_id")
            or item.get("action_id")
            or source
        )
        description = str(item.get("description") or item.get("summary") or item.get("text") or item.get("name") or source.replace("_", " "))
        confidence = item.get("confidence", item.get("score", item.get("progress_score", item.get("valence", 0.0))))
        try:
            confidence_value = float(confidence or 0.0)
        except Exception:
            confidence_value = 0.0

        evidence = item.get("evidence") or item.get("priors") or item.get("results") or item.get("sources") or []
        if isinstance(evidence, (str, bytes, bytearray)):
            evidence = [evidence]
        if isinstance(evidence, Mapping):
            evidence = [evidence]

        metadata = dict(item.get("metadata") or item.get("properties") or {})
        metadata.setdefault("source", source)
        metadata.setdefault("raw", dict(item))
        return {
            "goal_id": goal_id,
            "description": description,
            "confidence": confidence_value,
            "evidence": list(self._flatten(evidence)),
            "metadata": metadata,
        }

    @staticmethod
    def _context_to_records(result: Any) -> list[dict[str, Any]]:
        if not isinstance(result, Mapping):
            return []
        summary = result.get("summary") or result.get("context") or result.get("game_context")
        if summary is None:
            return []
        return [
            {
                "goal_id": "game_context",
                "description": str(summary),
                "confidence": 0.05,
                "evidence": [summary],
                "metadata": {"source": "game_context", "raw": dict(result)},
            }
        ]

    @staticmethod
    def _extract_sequence(result: Any) -> Sequence[Any]:
        if result is None:
            return []
        if isinstance(result, Mapping):
            for key in ("goal_evidence", "mechanic_priors", "results", "priors", "records", "items", "actions"):
                value = result.get(key)
                if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                    return value
            return []
        if isinstance(result, Sequence) and not isinstance(result, (str, bytes, bytearray)):
            return result
        return []

    @staticmethod
    def _flatten(items: Sequence[Any]) -> Iterable[str]:
        for item in items:
            if item is None:
                continue
            if isinstance(item, Mapping):
                yield str(item)
                continue
            if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
                yield from ArcGraphQueryPort._flatten(item)
                continue
            yield str(item)

    @staticmethod
    def _dedupe_records(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        deduped: list[dict[str, Any]] = []
        for record in records:
            marker = (str(record.get("goal_id")), str(record.get("description")))
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(record)
        return deduped

    @staticmethod
    def _serialize_entity(entity: PerceivedEntity) -> dict[str, Any]:
        return {"kind": entity.kind, "value": entity.value, "attributes": dict(entity.attributes)}

    @staticmethod
    def _serialize_candidate(candidate: Any) -> dict[str, Any] | None:
        if candidate is None:
            return None
        return {
            "action_id": getattr(candidate, "action_id", None),
            "goal_id": getattr(candidate, "goal_id", None),
            "score": getattr(candidate, "score", None),
            "rationale": getattr(candidate, "rationale", None),
            "expected_effect": getattr(candidate, "expected_effect", None),
            "metadata": dict(getattr(candidate, "metadata", {}) or {}),
        }

    @staticmethod
    def _goal_id_from_plan(plan: PlanningResult) -> str:
        if plan.candidate and plan.candidate.goal_id:
            return plan.candidate.goal_id
        for alternative in plan.alternatives:
            if alternative.goal_id:
                return alternative.goal_id
        return "arc-goal"

    @staticmethod
    def _vet_action_id(vet: VetDecision) -> str | None:
        if vet.candidate is not None:
            return vet.candidate.action_id
        if vet.alternative is not None:
            return vet.alternative.action_id
        return None

    @staticmethod
    def _goal_action_id(goal: ResolvedGoal | GoalHypothesis | None) -> str | None:
        if goal is None:
            return None
        metadata = goal.selected.metadata if isinstance(goal, ResolvedGoal) else goal.metadata
        for key in ("preferred_action", "selected_action", "action_id"):
            value = metadata.get(key)
            if value:
                return str(value)
        preferred_actions = metadata.get("preferred_actions")
        if isinstance(preferred_actions, Sequence) and not isinstance(preferred_actions, (str, bytes, bytearray)):
            for action_id in preferred_actions:
                if action_id:
                    return str(action_id)
        return None

    @staticmethod
    def _action_patterns(goal: ResolvedGoal | GoalHypothesis | None) -> list[dict[str, Any]]:
        if goal is None:
            return []
        metadata = goal.selected.metadata if isinstance(goal, ResolvedGoal) else goal.metadata
        patterns = metadata.get("action_patterns") or metadata.get("preferred_actions") or []
        if isinstance(patterns, Sequence) and not isinstance(patterns, (str, bytes, bytearray)):
            return [{"action_id": str(item), "pattern": str(item)} for item in patterns if item]
        if metadata.get("preferred_action"):
            preferred_action = str(metadata["preferred_action"])
            return [{"action_id": preferred_action, "pattern": preferred_action}]
        return []

    @staticmethod
    def _game_features(perception: PerceptionSnapshot) -> dict[str, Any]:
        return {
            "grid_shape": list(perception.grid_shape) if perception.grid_shape is not None else None,
            "grid_hash": perception.grid_hash,
            "loop_signal": bool(perception.loop_signal),
            "repeated_grid_count": perception.repeated_grid_count,
            "entity_count": len(perception.entities),
        }

    @staticmethod
    def _infer_archetype(perception: PerceptionSnapshot, goal: ResolvedGoal | GoalHypothesis | None) -> str:
        if goal is None:
            return str(perception.metadata.get("archetype") or "")
        metadata = goal.selected.metadata if isinstance(goal, ResolvedGoal) else goal.metadata
        archetype = metadata.get("archetype") or perception.metadata.get("archetype")
        return str(archetype or "")

    @staticmethod
    def _execution_step(execution: ExecutionResult) -> int:
        metadata = execution.metadata if isinstance(execution.metadata, Mapping) else {}
        for key in ("step", "step_num", "step_index", "execution_step"):
            value = metadata.get(key)
            if value is not None:
                try:
                    return int(value)
                except Exception:
                    continue
        return 0

    @staticmethod
    def _perception_step(perception: PerceptionSnapshot) -> int:
        metadata = perception.metadata if isinstance(perception.metadata, Mapping) else {}
        for key in ("step", "step_num", "step_index"):
            value = metadata.get(key)
            if value is not None:
                try:
                    return int(value)
                except Exception:
                    continue
        return 0

    @staticmethod
    def _observation_state(observation: Mapping[str, Any]) -> str:
        return str(observation.get("state") or observation.get("result_state") or "unknown")

    @staticmethod
    def _entities_affected(observation: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        entities = observation.get("entities_affected") if isinstance(observation, Mapping) else None
        if isinstance(entities, Sequence) and not isinstance(entities, (str, bytes, bytearray)):
            return [entity for entity in entities if isinstance(entity, Mapping)]
        return []


__all__ = ["ARC_V2_TOOL_NAMES", "ArcGraphQueryPort"]