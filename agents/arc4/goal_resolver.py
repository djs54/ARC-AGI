"""ARC v2 goal resolution with deterministic, graph-backed, and optional LLM tiers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .ports import GraphQueryPort, LLMMessage, LLMPort
from .types import GoalHypothesis, PerceptionSnapshot, PhaseResult, PhaseStatus, ResolvedGoal, WorkflowPhase, WorkflowState


@dataclass(slots=True)
class GoalResolverLimits:
    min_heuristic_confidence: float = 0.35
    graph_confidence_boost: float = 0.05
    ambiguity_gap: float = 0.12
    low_confidence_threshold: float = 0.7
    llm_patience_steps: int = 2
    max_hypotheses: int = 5
    goal_failure_threshold: int = 2
    goal_failure_decay_factor: float = 0.7


class GoalResolver:
    """Resolve the active ARC v2 goal through heuristics, graph evidence, then optional LLM help."""

    def __init__(self, limits: GoalResolverLimits | None = None) -> None:
        self._limits = limits or GoalResolverLimits()

    def resolve(
        self,
        state: WorkflowState,
        perception: PerceptionSnapshot,
        *,
        graph_port: GraphQueryPort | None = None,
        llm_port: LLMPort | None = None,
    ) -> PhaseResult[ResolvedGoal]:
        hypotheses = self._tier_one_hypotheses(state, perception)
        graph_evidence: list[dict[str, Any]] = []

        if graph_port is not None:
            hypotheses, graph_evidence = self._merge_graph_evidence(hypotheses, graph_port, perception)

        llm_applied = False
        llm_reason: str | None = None
        if llm_port is not None and self._should_escalate_to_llm(state, hypotheses):
            llm_patch = self._query_llm(llm_port, perception, hypotheses)
            if llm_patch is not None:
                hypotheses = self._merge_llm_patch(hypotheses, llm_patch)
                llm_applied = True
                llm_reason = llm_patch.get("reason")

        hypotheses = self._order_hypotheses(hypotheses)
        hypotheses = self._apply_failure_decay(state, hypotheses)
        hypotheses = self._order_hypotheses(hypotheses)
        hypotheses, grounding_gate_passed = self._apply_grounding_gate(state, perception, hypotheses)
        selected = hypotheses[0]
        alternatives = tuple(hypotheses[1:])

        metadata = {
            "hypotheses": [self._serialize_hypothesis(hypothesis) for hypothesis in hypotheses],
            "graph_evidence": graph_evidence,
            "llm_escalated": llm_applied,
            "llm_reason": llm_reason,
            "grounding_gate_passed": grounding_gate_passed,
        }
        return PhaseResult(
            phase=WorkflowPhase.RESOLVE,
            status=PhaseStatus.OK,
            payload=ResolvedGoal(
                selected=selected,
                alternatives=alternatives,
                grounding_gate_passed=grounding_gate_passed,
                metadata=metadata,
            ),
            metadata={"hypothesis_count": len(hypotheses)},
        )

    def _tier_one_hypotheses(self, state: WorkflowState, perception: PerceptionSnapshot) -> list[GoalHypothesis]:
        hypotheses: list[GoalHypothesis] = []

        for index, entity in enumerate(perception.entities[:3]):
            goal_id = self._slugify(f"{entity.kind}-{entity.value or index}")
            description = self._describe_entity_goal(entity.kind, entity.value, perception)
            confidence = min(0.75, self._limits.min_heuristic_confidence + 0.12 + (0.05 * (2 - min(index, 2))))
            hypotheses.append(
                GoalHypothesis(
                    goal_id=goal_id,
                    description=description,
                    confidence=confidence,
                    evidence=(f"entity:{entity.kind}:{entity.value}",),
                    metadata={"tier": 1, "entity_index": index, "attributes": dict(entity.attributes)},
                )
            )

        if not hypotheses:
            if perception.grid_shape is not None:
                rows, cols = perception.grid_shape
                hypotheses.append(
                    GoalHypothesis(
                        goal_id=f"grid-{rows}x{cols}",
                        description=f"Infer the structural goal of the {rows}x{cols} grid",
                        confidence=0.5,
                        evidence=(f"grid_shape:{rows}x{cols}",),
                        metadata={"tier": 1, "grid_shape": perception.grid_shape},
                    )
                )
            if perception.loop_signal or perception.repeated_grid_count > 0:
                hypotheses.append(
                    GoalHypothesis(
                        goal_id="loop-breaker",
                        description="Break the repeated state or find a progress-making action",
                        confidence=0.45,
                        evidence=("loop_signal",),
                        metadata={"tier": 1, "loop_signal": perception.loop_signal, "repeated_grid_count": perception.repeated_grid_count},
                    )
                )

        if not hypotheses:
            hypotheses.append(
                GoalHypothesis(
                    goal_id="unknown-structural-goal",
                    description="Infer the puzzle's visible structural objective",
                    confidence=self._limits.min_heuristic_confidence,
                    evidence=("fallback",),
                    metadata={"tier": 1, "fallback": True},
                )
            )

        return hypotheses[: self._limits.max_hypotheses]

    def _merge_graph_evidence(
        self,
        hypotheses: list[GoalHypothesis],
        graph_port: GraphQueryPort,
        perception: PerceptionSnapshot,
    ) -> tuple[list[GoalHypothesis], list[dict[str, Any]]]:
        raw_evidence = graph_port.fetch_goal_evidence(perception, hypotheses[0] if hypotheses else None)
        records = self._normalize_records(raw_evidence)
        if not records:
            return hypotheses, []

        merged = list(hypotheses)
        evidence_records: list[dict[str, Any]] = []
        for record in records:
            normalized = self._normalize_graph_record(record)
            evidence_records.append(normalized)
            merged = self._merge_single_record(merged, normalized)
        return merged, evidence_records

    def _merge_single_record(self, hypotheses: list[GoalHypothesis], record: dict[str, Any]) -> list[GoalHypothesis]:
        goal_id = record.get("goal_id")
        description = record.get("description")
        confidence = float(record.get("confidence", 0.0) or 0.0)
        evidence = tuple(str(item) for item in record.get("evidence", ()) if item is not None)
        metadata = dict(record.get("metadata", {}))
        metadata.setdefault("tier", 2)
        metadata["graph_evidence"] = True

        boost = min(0.95, confidence + self._limits.graph_confidence_boost)
        updated: list[GoalHypothesis] = []
        matched = False
        for hypothesis in hypotheses:
            if goal_id and hypothesis.goal_id == goal_id:
                matched = True
                updated.append(
                    GoalHypothesis(
                        goal_id=hypothesis.goal_id,
                        description=description or hypothesis.description,
                        confidence=max(hypothesis.confidence, boost),
                        evidence=self._merge_evidence(hypothesis.evidence, evidence),
                        metadata=self._merge_metadata(hypothesis.metadata, metadata),
                    )
                )
                continue
            updated.append(hypothesis)

        if not matched:
            updated.append(
                GoalHypothesis(
                    goal_id=goal_id or self._slugify(description or "graph-goal"),
                    description=description or "Graph-backed goal hypothesis",
                    confidence=boost or self._limits.min_heuristic_confidence,
                    evidence=evidence,
                    metadata=metadata,
                )
            )

        return updated

    def _should_escalate_to_llm(self, state: WorkflowState, hypotheses: Sequence[GoalHypothesis]) -> bool:
        if len(hypotheses) < 2:
            return bool(hypotheses and hypotheses[0].confidence < self._limits.low_confidence_threshold and state.consecutive_no_progress_count >= self._limits.llm_patience_steps)

        ordered = self._order_hypotheses(hypotheses)
        top = ordered[0]
        runner_up = ordered[1]
        ambiguous = (top.confidence - runner_up.confidence) <= self._limits.ambiguity_gap
        under_confident = top.confidence < self._limits.low_confidence_threshold and state.consecutive_no_progress_count >= self._limits.llm_patience_steps
        return ambiguous or under_confident

    def _query_llm(
        self,
        llm_port: LLMPort,
        perception: PerceptionSnapshot,
        hypotheses: Sequence[GoalHypothesis],
    ) -> dict[str, Any] | None:
        messages = [
            LLMMessage(
                role="system",
                content="Resolve the ARC goal ambiguity by selecting the best candidate goal_id and confidence.",
            ),
            LLMMessage(
                role="user",
                content=json.dumps(
                    {
                        "grid_hash": perception.grid_hash,
                        "grid_shape": perception.grid_shape,
                        "candidates": [self._serialize_hypothesis(hypothesis) for hypothesis in self._order_hypotheses(hypotheses)],
                        "required_fields": ["goal_id", "confidence", "reason"],
                    },
                    sort_keys=True,
                ),
            ),
        ]
        response = llm_port.chat(messages)
        return self._parse_llm_response(response)

    def _merge_llm_patch(
        self,
        hypotheses: list[GoalHypothesis],
        patch: dict[str, Any],
    ) -> list[GoalHypothesis]:
        goal_id = patch.get("goal_id")
        confidence = float(patch.get("confidence", 0.0) or 0.0)
        reason = str(patch.get("reason", "") or "")
        evidence = tuple(str(item) for item in patch.get("evidence", ()) if item is not None)
        updated: list[GoalHypothesis] = []
        matched = False
        for hypothesis in hypotheses:
            if goal_id and hypothesis.goal_id == goal_id:
                matched = True
                metadata = self._merge_metadata(hypothesis.metadata, {"tier": 3, "llm_reason": reason, "llm_patch": True})
                updated.append(
                    GoalHypothesis(
                        goal_id=hypothesis.goal_id,
                        description=patch.get("description") or hypothesis.description,
                        confidence=max(hypothesis.confidence, confidence),
                        evidence=self._merge_evidence(hypothesis.evidence, evidence),
                        metadata=metadata,
                    )
                )
                continue
            updated.append(hypothesis)

        if not matched and goal_id:
            updated.append(
                GoalHypothesis(
                    goal_id=goal_id,
                    description=patch.get("description") or reason or "LLM-backed goal hypothesis",
                    confidence=confidence,
                    evidence=evidence,
                    metadata={"tier": 3, "llm_reason": reason, "llm_patch": True},
                )
            )

        return updated

    def _apply_failure_decay(
        self,
        state: WorkflowState,
        hypotheses: list[GoalHypothesis],
    ) -> list[GoalHypothesis]:
        """Decay confidence for hypotheses whose goal has repeatedly failed to progress.

        Mirrors plan_generator.py's repeat_decay_factor pattern (A131): a goal that has
        been active for `goal_failure_threshold` or more consecutive no-progress
        evaluations loses ranking priority relative to untested alternatives, so a
        persistently-failing goal doesn't stay pinned at the top of the hypothesis list
        forever (A152).
        """
        decayed: list[GoalHypothesis] = []
        for hypothesis in hypotheses:
            failures = state.goal_failure_counts.get(hypothesis.goal_id, 0)
            if failures < self._limits.goal_failure_threshold:
                decayed.append(hypothesis)
                continue
            decay = self._limits.goal_failure_decay_factor ** (failures - self._limits.goal_failure_threshold + 1)
            decayed.append(
                GoalHypothesis(
                    goal_id=hypothesis.goal_id,
                    description=hypothesis.description,
                    confidence=hypothesis.confidence * decay,
                    evidence=hypothesis.evidence,
                    metadata=self._merge_metadata(hypothesis.metadata, {"failure_decay_applied": True, "failure_count": failures}),
                )
            )
        return decayed

    def _apply_grounding_gate(
        self,
        state: WorkflowState,
        perception: PerceptionSnapshot,
        hypotheses: list[GoalHypothesis],
    ) -> tuple[list[GoalHypothesis], bool]:
        if state.active_goal is None:
            return hypotheses, True

        progress_made = self._observed_progress(state, perception)
        if progress_made:
            return hypotheses, True

        active_goal_id = state.active_goal.selected.goal_id
        ceiling = state.active_goal.selected.confidence
        clamped: list[GoalHypothesis] = []
        applied = False
        for hypothesis in hypotheses:
            # Only clamp upward drift on the *same* goal that was already active — a
            # fresh alternative hypothesis (including one promoted by failure decay
            # elsewhere in this cycle) must not be suppressed down to the stalled
            # goal's ceiling, or a persistently-failing goal could never be displaced
            # (A152 death-spiral risk called out in the plan's Step 4).
            if hypothesis.goal_id == active_goal_id and hypothesis.confidence > ceiling:
                applied = True
                clamped.append(
                    GoalHypothesis(
                        goal_id=hypothesis.goal_id,
                        description=hypothesis.description,
                        confidence=ceiling,
                        evidence=hypothesis.evidence,
                        metadata=self._merge_metadata(hypothesis.metadata, {"grounding_gate": "clamped", "grounding_ceiling": ceiling}),
                    )
                )
                continue
            clamped.append(hypothesis)
        return clamped, not applied

    def _observed_progress(self, state: WorkflowState, perception: PerceptionSnapshot) -> bool:
        if perception.loop_signal or perception.repeated_grid_count > 0:
            return False
        if state.previous_grid_hash is None:
            return True
        return perception.grid_hash != state.previous_grid_hash

    @staticmethod
    def _order_hypotheses(hypotheses: Sequence[GoalHypothesis]) -> list[GoalHypothesis]:
        return sorted(hypotheses, key=lambda hypothesis: (-hypothesis.confidence, hypothesis.goal_id))

    @staticmethod
    def _serialize_hypothesis(hypothesis: GoalHypothesis) -> dict[str, Any]:
        return {
            "goal_id": hypothesis.goal_id,
            "description": hypothesis.description,
            "confidence": hypothesis.confidence,
            "evidence": list(hypothesis.evidence),
            "metadata": dict(hypothesis.metadata),
        }

    @staticmethod
    def _merge_evidence(existing: Sequence[str], additional: Iterable[str]) -> tuple[str, ...]:
        merged: list[str] = list(existing)
        for item in additional:
            if item and item not in merged:
                merged.append(item)
        return tuple(merged)

    @staticmethod
    def _merge_metadata(existing: Mapping[str, Any], additional: Mapping[str, Any]) -> dict[str, Any]:
        merged = dict(existing)
        for key, value in additional.items():
            if key == "graph_evidence" and key in merged:
                continue
            merged[key] = value
        return merged

    @staticmethod
    def _describe_entity_goal(kind: str, value: str, perception: PerceptionSnapshot) -> str:
        value_text = value or kind or "entity"
        if perception.grid_shape is not None:
            rows, cols = perception.grid_shape
            return f"Use the {kind} {value_text} to satisfy the {rows}x{cols} grid objective"
        return f"Use the {kind} {value_text} to satisfy the visible objective"

    @staticmethod
    def _normalize_records(raw: Any) -> list[Any]:
        if raw is None:
            return []
        if isinstance(raw, GoalHypothesis):
            return [raw]
        if isinstance(raw, Mapping):
            if "goals" in raw and isinstance(raw["goals"], Sequence):
                return list(raw["goals"])
            if "hypotheses" in raw and isinstance(raw["hypotheses"], Sequence):
                return list(raw["hypotheses"])
            return [raw]
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            return list(raw)
        return [raw]

    @staticmethod
    def _normalize_graph_record(record: Any) -> dict[str, Any]:
        if isinstance(record, GoalHypothesis):
            return {
                "goal_id": record.goal_id,
                "description": record.description,
                "confidence": record.confidence,
                "evidence": list(record.evidence),
                "metadata": dict(record.metadata),
            }

        if not isinstance(record, Mapping):
            return {
                "goal_id": GoalResolver._slugify(str(record)),
                "description": str(record),
                "confidence": 0.0,
                "evidence": [],
                "metadata": {"raw_record": record},
            }

        metadata = dict(record.get("metadata") or record.get("properties") or {})
        evidence = record.get("evidence") or record.get("evidence_path_ids") or record.get("sources") or []
        if isinstance(evidence, (str, bytes, bytearray)):
            evidence = [evidence]
        confidence = record.get("confidence", record.get("score", record.get("probability", 0.0)))
        return {
            "goal_id": str(record.get("goal_id") or record.get("id") or record.get("key") or GoalResolver._slugify(str(record.get("description") or record.get("claim") or "graph-goal"))),
            "description": str(record.get("description") or record.get("claim") or record.get("text") or "Graph-backed goal hypothesis"),
            "confidence": float(confidence or 0.0),
            "evidence": list(evidence),
            "metadata": metadata,
            "reason": record.get("reason"),
        }

    @staticmethod
    def _parse_llm_response(response: str) -> dict[str, Any] | None:
        if not response:
            return None
        try:
            parsed = json.loads(response)
        except json.JSONDecodeError:
            parsed = None

        if isinstance(parsed, Mapping) and parsed.get("goal_id"):
            return dict(parsed)

        goal_match = re.search(r"goal[_\s-]*id\s*[:=]\s*([A-Za-z0-9_.-]+)", response, re.IGNORECASE)
        confidence_match = re.search(r"confidence\s*[:=]\s*([0-9]*\.?[0-9]+)", response, re.IGNORECASE)
        reason_match = re.search(r"reason\s*[:=]\s*(.{1,200})", response, re.IGNORECASE)
        if goal_match or confidence_match or reason_match:
            return {
                "goal_id": goal_match.group(1) if goal_match else None,
                "confidence": float(confidence_match.group(1)) if confidence_match else 0.0,
                "reason": reason_match.group(1).strip() if reason_match else response.strip(),
            }
        return None

    @staticmethod
    def _slugify(value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
        return slug or "goal"