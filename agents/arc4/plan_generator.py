"""ARC v2 plan generation with deterministic ranking and optional LLM escalation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .ports import GraphQueryPort, LLMMessage, LLMPort
from .types import GoalHypothesis, PerceptionSnapshot, PhaseResult, PhaseStatus, PlanCandidate, PlanningResult, ResolvedGoal, WorkflowPhase, WorkflowState


@dataclass(slots=True)
class PlanGeneratorLimits:
    untested_bonus: float = 0.22
    goal_alignment_bonus: float = 0.18
    graph_evidence_bonus: float = 0.12
    repeat_attempt_penalty: float = 0.04
    falsification_penalty: float = 0.16
    replan_feedback_bonus: float = 0.3
    llm_low_score_threshold: float = 0.4
    llm_patience_steps: int = 2
    max_candidates: int = 6
    # A131: Exponential decay for repeated actions
    repeat_decay_factor: float = 0.6  # score *= decay^attempts (0.6^3 ≈ 0.22)
    force_explore_after: int = 3  # force untested action after N consecutive same-action


@dataclass(slots=True)
class _CandidateRecord:
    action_id: str
    score: float
    rationale: str
    expected_effect: str | None
    metadata: dict[str, Any]


class PlanGenerator:
    """Rank candidate actions using graph hints, goal context, and local history."""

    def __init__(self, limits: PlanGeneratorLimits | None = None) -> None:
        self._limits = limits or PlanGeneratorLimits()

    def __call__(
        self,
        state: WorkflowState,
        perception: PerceptionSnapshot,
        goal: ResolvedGoal,
        *,
        graph_port: GraphQueryPort | None = None,
        llm_port: LLMPort | None = None,
    ) -> PhaseResult[PlanningResult]:
        return self.generate(state, perception, goal, graph_port=graph_port, llm_port=llm_port)

    def generate(
        self,
        state: WorkflowState,
        perception: PerceptionSnapshot,
        goal: ResolvedGoal,
        *,
        graph_port: GraphQueryPort | None = None,
        llm_port: LLMPort | None = None,
    ) -> PhaseResult[PlanningResult]:
        graph_records = self._fetch_graph_records(graph_port, perception, goal)
        mechanic_actions = self._extract_mechanic_prior_actions(graph_records)
        available_actions = self._available_actions(perception, goal, graph_records, graph_port=graph_port)
        candidates = self._build_candidates(state, perception, goal, available_actions, graph_records, mechanic_action_set=set(mechanic_actions), graph_port=graph_port)

        if llm_port is not None and candidates and candidates[0].score < self._limits.llm_low_score_threshold:
            llm_patch = self._query_llm(llm_port, perception, goal, candidates)
            if llm_patch is not None:
                candidates = self._apply_llm_patch(candidates, llm_patch)

        ranked = self._rank_candidates(candidates)

        # A131: Force exploration when top candidate is excessively repeated
        forced_explore = False
        if ranked and self._should_force_explore(state, ranked[0]):
            untested = [c for c in ranked if c.metadata.get("untested")]
            if untested:
                # Promote the best untested candidate to top
                promoted = untested[0]
                ranked = [promoted] + [c for c in ranked if c is not promoted]
                forced_explore = True

        selected = ranked[0] if ranked else None
        alternatives = tuple(self._to_plan_candidate(candidate, goal.selected.goal_id) for candidate in ranked[1:])

        payload = PlanningResult(
            candidate=self._to_plan_candidate(selected, goal.selected.goal_id) if selected is not None else None,
            alternatives=alternatives,
            needs_vet=True,
            metadata={
                "goal_contract": self._serialize_goal(goal),
                "graph_records": graph_records,
                "ranked_candidates": [self._serialize_candidate(candidate) for candidate in ranked],
                "replan_feedback_applied": bool(state.replan_passes and state.latest_veto_alternative and selected and selected.action_id == state.latest_veto_alternative.action_id),
                "forced_explore": forced_explore,
            },
        )
        return PhaseResult(phase=WorkflowPhase.PLAN, status=PhaseStatus.OK, payload=payload)

    def _build_candidates(
        self,
        state: WorkflowState,
        perception: PerceptionSnapshot,
        goal: ResolvedGoal,
        available_actions: Sequence[str],
        graph_records: Sequence[dict[str, Any]],
        *,
        mechanic_action_set: set[str] | None = None,
        graph_port: GraphQueryPort | None = None,
    ) -> list[_CandidateRecord]:
        if mechanic_action_set is None:
            mechanic_action_set = set()
        records_by_action = {record["action_id"]: record for record in graph_records if record.get("action_id")}
        candidates: list[_CandidateRecord] = []

        for action_id in available_actions[: self._limits.max_candidates]:
            graph_record = records_by_action.get(action_id, {})
            attempts = int(state.action_attempt_counts.get(action_id, 0))
            falsifications = int(state.action_falsification_counts.get(action_id, 0))
            is_untested = attempts == 0
            repeated_falsified = falsifications >= 2
            goal_alignment = self._action_matches_goal(action_id, goal)
            graph_score = float(graph_record.get("confidence", graph_record.get("score", 0.0)) or 0.0)

            # A135: Enrich graph_score with per-action evidence from the graph
            graph_evidence: dict[str, Any] = {}
            if graph_port is not None:
                try:
                    graph_evidence = graph_port.fetch_per_action_evidence(action_id)
                    evidence_confidence = graph_evidence.get("confidence", 0.0)
                    evidence_contradictions = graph_evidence.get("contradictions", 0)
                    evidence_supports = graph_evidence.get("supports", 0)
                    # Use graph evidence confidence if it's stronger than goal-level score
                    if evidence_confidence > graph_score:
                        graph_score = evidence_confidence
                    # Deduct for accumulated contradictions from graph
                    if evidence_contradictions > evidence_supports:
                        graph_score -= self._limits.falsification_penalty * (evidence_contradictions - evidence_supports)
                except Exception:
                    pass  # graph unavailable — use existing score

            score = graph_score
            if goal_alignment:
                score += self._limits.goal_alignment_bonus
            if is_untested:
                score += self._limits.untested_bonus
            else:
                # A131: Exponential decay for repeated actions instead of weak linear penalty
                decay = self._limits.repeat_decay_factor ** attempts
                score *= decay
                score -= min(self._limits.repeat_attempt_penalty * attempts, 0.18)
            if falsifications:
                score -= min(self._limits.falsification_penalty * falsifications, 0.55)
            if state.latest_veto_alternative is not None and state.replan_passes == 1 and action_id == state.latest_veto_alternative.action_id:
                score += self._limits.replan_feedback_bonus

            if graph_record.get("goal_id") == goal.selected.goal_id:
                score += self._limits.graph_evidence_bonus

            rationale_parts = [graph_record.get("rationale") or f"consider {action_id} for {goal.selected.goal_id}"]
            if goal_alignment:
                rationale_parts.append("matches goal contract")
            if is_untested:
                rationale_parts.append("untested")
            if repeated_falsified:
                rationale_parts.append(f"repeatedly falsified x{falsifications}")
            if state.latest_veto_alternative is not None and action_id == state.latest_veto_alternative.action_id:
                rationale_parts.append("veto feedback")

            candidates.append(
                _CandidateRecord(
                    action_id=action_id,
                    score=score,
                    rationale="; ".join(rationale_parts),
                    expected_effect=self._expected_effect(graph_record, goal, action_id),
                    metadata={
                        "attempt_count": attempts,
                        "falsification_count": falsifications,
                        "goal_alignment": goal_alignment,
                        "graph_record": dict(graph_record),
                        "graph_evidence": graph_evidence,
                        "perception_grid_hash": perception.grid_hash,
                        "untested": is_untested,
                        "repeated_falsified": repeated_falsified,
                        "replan_passes": state.replan_passes,
                        "mechanic_prior_source": action_id in mechanic_action_set,
                    },
                )
            )

        if not candidates:
            candidates.append(self._fallback_candidate(state, goal, perception))

        if state.latest_veto_alternative is not None and state.replan_passes == 1:
            veto_action = state.latest_veto_alternative.action_id
            if veto_action not in {candidate.action_id for candidate in candidates}:
                candidates.append(
                    _CandidateRecord(
                        action_id=veto_action,
                        score=self._limits.replan_feedback_bonus,
                        rationale=f"replan feedback suggests {veto_action}",
                        expected_effect=state.latest_veto_alternative.expected_effect,
                        metadata={"replan_feedback": True, "source": "veto_alternative"},
                    )
                )

        return candidates

    def _fallback_candidate(self, state: WorkflowState, goal: ResolvedGoal, perception: PerceptionSnapshot) -> _CandidateRecord:
        action_id = self._slugify(f"probe-{goal.selected.goal_id}")
        return _CandidateRecord(
            action_id=action_id,
            score=0.1,
            rationale=f"fallback probe for {goal.selected.goal_id}",
            expected_effect=goal.selected.description,
            metadata={
                "attempt_count": state.action_attempt_counts.get(action_id, 0),
                "falsification_count": state.action_falsification_counts.get(action_id, 0),
                "goal_alignment": True,
                "graph_record": {},
                "perception_grid_hash": perception.grid_hash,
                "untested": True,
                "repeated_falsified": False,
                "fallback": True,
            },
        )

    @staticmethod
    def _extract_mechanic_prior_actions(graph_records: Sequence[dict[str, Any]]) -> list[str]:
        """Extract individual action IDs from mechanic prior action_set fields."""
        actions: list[str] = []
        for record in graph_records:
            metadata = record.get("metadata") or {}
            if metadata.get("source") != "mechanic_priors":
                continue
            raw = metadata.get("raw") or {}
            mechanics = raw.get("mechanics") or []
            if isinstance(mechanics, Mapping):
                mechanics = [mechanics]
            for mechanic in mechanics:
                if not isinstance(mechanic, Mapping):
                    continue
                action_set = mechanic.get("action_set") or ""
                if isinstance(action_set, str):
                    for action_id in action_set.split(","):
                        action_id = action_id.strip()
                        if action_id and action_id not in actions:
                            actions.append(action_id)
        return actions

    def _fetch_graph_records(
        self,
        graph_port: GraphQueryPort | None,
        perception: PerceptionSnapshot,
        goal: ResolvedGoal,
    ) -> list[dict[str, Any]]:
        if graph_port is None:
            return []
        raw = graph_port.fetch_goal_evidence(perception, goal)
        return self._normalize_records(raw)

    def _available_actions(
        self,
        perception: PerceptionSnapshot,
        goal: ResolvedGoal,
        graph_records: Sequence[dict[str, Any]],
        *,
        graph_port: GraphQueryPort | None = None,
    ) -> list[str]:
        candidates: list[str] = []
        for source in (
            perception.observation.get("available_actions") if isinstance(perception.observation, Mapping) else None,
            perception.metadata.get("available_actions"),
            goal.selected.metadata.get("preferred_actions"),
            goal.selected.metadata.get("available_actions"),
            goal.metadata.get("available_actions"),
            [record["action_id"] for record in graph_records if record.get("action_id")],
        ):
            if isinstance(source, Sequence) and not isinstance(source, (str, bytes)):
                for action_id in source:
                    action_text = str(action_id)
                    if action_text and action_text not in candidates:
                        candidates.append(action_text)

        # A136: Extract actions from mechanic prior action_set fields
        for action_id in self._extract_mechanic_prior_actions(graph_records):
            if action_id not in candidates:
                candidates.append(action_id)

        # A135: Merge untested actions from the graph world model
        if graph_port is not None:
            try:
                untested = graph_port.fetch_untested_actions()
                for action_id in untested:
                    action_text = str(action_id)
                    if action_text and action_text not in candidates:
                        candidates.append(action_text)
            except Exception:
                pass  # graph unavailable — fall through to existing sources

        if not candidates:
            candidates.append(self._slugify(f"probe-{goal.selected.goal_id}"))
        return candidates

    def _query_llm(
        self,
        llm_port: LLMPort,
        perception: PerceptionSnapshot,
        goal: ResolvedGoal,
        candidates: Sequence[_CandidateRecord],
    ) -> dict[str, Any] | None:
        messages = [
            LLMMessage(
                role="system",
                content="Pick the best ARC action_id and explain why it should be tried next.",
            ),
            LLMMessage(
                role="user",
                content=json.dumps(
                    {
                        "grid_hash": perception.grid_hash,
                        "goal": self._serialize_goal(goal),
                        "candidates": [self._serialize_candidate(candidate) for candidate in candidates],
                        "required_fields": ["action_id", "reason"],
                    },
                    sort_keys=True,
                ),
            ),
        ]
        response = llm_port.chat(messages)
        try:
            parsed = json.loads(response)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) and parsed.get("action_id") else None

    def _apply_llm_patch(self, candidates: Sequence[_CandidateRecord], patch: dict[str, Any]) -> list[_CandidateRecord]:
        action_id = str(patch.get("action_id"))
        reason = str(patch.get("reason", "") or "")
        bonus = float(patch.get("confidence", 0.0) or 0.0)
        updated: list[_CandidateRecord] = []
        matched = False
        for candidate in candidates:
            if candidate.action_id == action_id:
                matched = True
                updated.append(
                    _CandidateRecord(
                        action_id=candidate.action_id,
                        score=max(candidate.score, bonus + self._limits.replan_feedback_bonus),
                        rationale="; ".join(part for part in [candidate.rationale, reason, "llm guidance"] if part),
                        expected_effect=patch.get("expected_effect") or candidate.expected_effect,
                        metadata={**candidate.metadata, "llm_guidance": True, "llm_reason": reason},
                    )
                )
                continue
            updated.append(candidate)
        if not matched:
            updated.append(
                _CandidateRecord(
                    action_id=action_id,
                    score=max(self._limits.replan_feedback_bonus, bonus),
                    rationale=reason or f"llm suggested {action_id}",
                    expected_effect=patch.get("expected_effect"),
                    metadata={"llm_guidance": True, "llm_reason": reason},
                )
            )
        return updated

    def _should_force_explore(self, state: WorkflowState, top_candidate: _CandidateRecord) -> bool:
        """Force exploration when the top candidate has been used too many consecutive times."""
        attempts = int(state.action_attempt_counts.get(top_candidate.action_id, 0))
        return attempts >= self._limits.force_explore_after

    def _rank_candidates(self, candidates: Sequence[_CandidateRecord]) -> list[_CandidateRecord]:
        return sorted(candidates, key=self._candidate_sort_key)

    def _candidate_sort_key(self, candidate: _CandidateRecord) -> tuple[float, int, int, str]:
        metadata = candidate.metadata
        untested = bool(metadata.get("untested"))
        repeated_falsified = bool(metadata.get("repeated_falsified"))
        bucket = 0 if untested else 2 if repeated_falsified else 1
        return (-candidate.score, bucket, int(metadata.get("attempt_count", 0)), candidate.action_id)

    @staticmethod
    def _action_matches_goal(action_id: str, goal: ResolvedGoal) -> bool:
        preferred_actions = goal.selected.metadata.get("preferred_actions", ())
        if isinstance(preferred_actions, Sequence) and not isinstance(preferred_actions, (str, bytes)):
            return action_id in {str(item) for item in preferred_actions}
        goal_hint = goal.selected.metadata.get("preferred_action")
        return action_id == str(goal_hint) if goal_hint is not None else False

    @staticmethod
    def _expected_effect(graph_record: Mapping[str, Any], goal: ResolvedGoal, action_id: str) -> str | None:
        effect = graph_record.get("expected_effect") or graph_record.get("effect")
        if effect is not None:
            return str(effect)
        if graph_record.get("goal_id") == goal.selected.goal_id:
            return goal.selected.description
        return f"advance {goal.selected.goal_id} with {action_id}"

    @staticmethod
    def _serialize_goal(goal: ResolvedGoal) -> dict[str, Any]:
        return {
            "selected": PlanGenerator._serialize_hypothesis(goal.selected),
            "alternatives": [PlanGenerator._serialize_hypothesis(hypothesis) for hypothesis in goal.alternatives],
            "grounding_gate_passed": goal.grounding_gate_passed,
            "metadata": dict(goal.metadata),
        }

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
    def _serialize_candidate(candidate: _CandidateRecord) -> dict[str, Any]:
        return {
            "action_id": candidate.action_id,
            "score": candidate.score,
            "rationale": candidate.rationale,
            "expected_effect": candidate.expected_effect,
            "metadata": dict(candidate.metadata),
        }

    @staticmethod
    def _to_plan_candidate(candidate: _CandidateRecord | None, goal_id: str) -> PlanCandidate | None:
        if candidate is None:
            return None
        metadata = dict(candidate.metadata)
        metadata.setdefault("goal_id", goal_id)
        return PlanCandidate(
            action_id=candidate.action_id,
            goal_id=goal_id,
            score=candidate.score,
            rationale=candidate.rationale,
            expected_effect=candidate.expected_effect,
            metadata=metadata,
        )

    @staticmethod
    def _normalize_records(raw: Any) -> list[dict[str, Any]]:
        if raw is None:
            return []
        if isinstance(raw, Mapping):
            actions = raw.get("actions")
            if isinstance(actions, Sequence) and not isinstance(actions, (str, bytes)):
                return [PlanGenerator._normalize_record(record) for record in actions if isinstance(record, Mapping)]
            if raw.get("action_id"):
                return [PlanGenerator._normalize_record(raw)]
            return []
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            return [PlanGenerator._normalize_record(record) for record in raw if isinstance(record, Mapping)]
        return []

    @staticmethod
    def _normalize_record(record: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(record)
        if "action_id" in normalized:
            normalized["action_id"] = str(normalized["action_id"])
        if "confidence" in normalized:
            normalized["confidence"] = float(normalized["confidence"] or 0.0)
        if "score" in normalized:
            normalized["score"] = float(normalized["score"] or 0.0)
        return normalized

    @staticmethod
    def _slugify(text: str) -> str:
        slug = [character.lower() if character.isalnum() else "-" for character in text]
        collapsed = "".join(slug).strip("-")
        while "--" in collapsed:
            collapsed = collapsed.replace("--", "-")
        return collapsed or "probe"