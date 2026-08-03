"""ARC v2 plan generation with deterministic ranking and optional LLM escalation."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)

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
    click_target_limit: int = 3


@dataclass(slots=True)
class _CandidateRecord:
    action_id: str
    book_id: str
    payload: dict[str, Any]
    score: float
    rationale: str
    expected_effect: str | None
    predicted_outcome: dict[str, Any]
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

        # A151: build (x, y) -> attempt_count map from ACTION6@x,y book_ids so click
        # target generation is aware of coordinates already attempted this run.
        attempted_click_coords: dict[tuple[int, int], int] = {}
        for book_id, count in state.action_attempt_counts.items():
            if book_id.startswith("ACTION6@") and count > 0:
                coord_part = book_id[len("ACTION6@") :]
                try:
                    x_str, y_str = coord_part.split(",", 1)
                    attempted_click_coords[(int(x_str), int(y_str))] = count
                except ValueError:
                    continue

        for action_id in available_actions[: self._limits.max_candidates]:
            graph_record = records_by_action.get(action_id, {})
            goal_alignment = self._action_matches_goal(action_id, goal)

            # A135: Enrich graph_score with per-action evidence from the graph
            graph_evidence: dict[str, Any] = {}
            graph_score = float(graph_record.get("confidence", graph_record.get("score", 0.0)) or 0.0)
            if graph_port is not None:
                try:
                    graph_evidence = graph_port.fetch_per_action_evidence(action_id)
                    evidence_confidence = graph_evidence.get("confidence", 0.0)
                    evidence_contradictions = graph_evidence.get("contradictions", 0)
                    evidence_supports = graph_evidence.get("supports", 0)
                    if evidence_confidence > graph_score:
                        graph_score = evidence_confidence
                    if evidence_contradictions > evidence_supports:
                        graph_score -= self._limits.falsification_penalty * (evidence_contradictions - evidence_supports)
                except Exception:
                    pass

            target_variants: list[tuple[str, dict[str, Any], dict[str, Any]]] = [(action_id, {}, {})]
            if action_id == "ACTION6":
                click_targets = self._click_targets(
                    perception,
                    limit=self._limits.click_target_limit,
                    attempted_coords=attempted_click_coords,
                )
                if click_targets:
                    target_variants = [
                        (
                            f"ACTION6@{target['x']},{target['y']}",
                            {"x": int(target["x"]), "y": int(target["y"])},
                            dict(target),
                        )
                        for target in click_targets
                    ]
                else:
                    target_variants = [("ACTION6@32,32", {"x": 32, "y": 32}, {"entity_kind": "fallback", "entity_color": "unknown"})]

            for book_id, payload, target_info in target_variants:
                attempts = int(state.action_attempt_counts.get(book_id, 0))
                falsifications = int(state.action_falsification_counts.get(book_id, 0))
                is_untested = attempts == 0
                repeated_falsified = falsifications >= 2

                score = graph_score
                if goal_alignment:
                    score += self._limits.goal_alignment_bonus
                if is_untested:
                    score += self._limits.untested_bonus
                else:
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
                if action_id == "ACTION6":
                    rationale_parts = [
                        f"click {target_info.get('entity_kind', 'entity')} color={target_info.get('entity_color', 'unknown')} at ({payload.get('x', 32)},{payload.get('y', 32)})"
                    ]
                if goal_alignment:
                    rationale_parts.append("matches goal contract")
                if is_untested:
                    rationale_parts.append("untested")
                if repeated_falsified:
                    rationale_parts.append(f"repeatedly falsified x{falsifications}")
                if state.latest_veto_alternative is not None and action_id == state.latest_veto_alternative.action_id:
                    rationale_parts.append("veto feedback")

                predicted_outcome = self._predicted_outcome(graph_record, graph_evidence, is_untested)
                expected_effect = f"{action_id}: expect {predicted_outcome.get('kind', 'grid_change')} (p={float(predicted_outcome.get('confidence', 0.0)):.2f})"

                candidates.append(
                    _CandidateRecord(
                        action_id=action_id,
                        book_id=book_id,
                        payload=payload,
                        score=score,
                        rationale="; ".join(rationale_parts),
                        expected_effect=expected_effect,
                        predicted_outcome=predicted_outcome,
                        metadata={
                            "book_id": book_id,
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
                            **target_info,
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
                        book_id=str((state.latest_veto_alternative.metadata or {}).get("book_id") or veto_action),
                        payload=dict(state.latest_veto_alternative.payload or {}),
                        score=self._limits.replan_feedback_bonus,
                        rationale=f"replan feedback suggests {veto_action}",
                        expected_effect=state.latest_veto_alternative.expected_effect,
                        predicted_outcome=dict(state.latest_veto_alternative.predicted_outcome or {}),
                        metadata={
                            "book_id": str((state.latest_veto_alternative.metadata or {}).get("book_id") or veto_action),
                            "replan_feedback": True,
                            "source": "veto_alternative",
                        },
                    )
                )

        return candidates

    def _fallback_candidate(self, state: WorkflowState, goal: ResolvedGoal, perception: PerceptionSnapshot) -> _CandidateRecord:
        action_id = self._slugify(f"probe-{goal.selected.goal_id}")
        return _CandidateRecord(
            action_id=action_id,
            book_id=action_id,
            payload={},
            score=0.1,
            rationale=f"fallback probe for {goal.selected.goal_id}",
            expected_effect=goal.selected.description,
            predicted_outcome={"kind": "grid_change", "confidence": 0.3},
            metadata={
                "book_id": action_id,
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
        obs_actions = perception.observation.get("available_actions") if isinstance(perception.observation, Mapping) else None
        logger.info("PLANNER obs_available_actions=%s", obs_actions)
        for source in (
            obs_actions,
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

        # Build authoritative action mask from the API observation.
        # When the API tells us which actions exist, use it to filter
        # inferred sources (mechanic priors, graph) that may include
        # phantom actions from similar but different games.
        api_action_set: set[str] | None = None
        if isinstance(obs_actions, Sequence) and obs_actions:
            api_action_set = set(str(a) for a in obs_actions)

        # A136: Extract actions from mechanic prior action_set fields
        mechanic_prior_actions = self._extract_mechanic_prior_actions(graph_records)
        for action_id in mechanic_prior_actions:
            if action_id not in candidates:
                # Filter against API mask — don't inject phantom actions
                if api_action_set is not None and action_id not in api_action_set:
                    logger.debug("PLANNER filtered phantom mechanic action %s (not in API actions %s)", action_id, api_action_set)
                    continue
                candidates.append(action_id)

        # A135: Merge untested actions from the graph world model
        if graph_port is not None:
            try:
                untested = graph_port.fetch_untested_actions()
                for action_id in untested:
                    action_text = str(action_id)
                    if action_text and action_text not in candidates:
                        # Filter against API mask
                        if api_action_set is not None and action_text not in api_action_set:
                            continue
                        candidates.append(action_text)
            except Exception:
                pass  # graph unavailable — fall through to existing sources

        if not candidates:
            candidates.append(self._slugify(f"probe-{goal.selected.goal_id}"))
        logger.info("PLANNER final candidates=%s (from obs=%s, mechanic=%s)", candidates, obs_actions, mechanic_prior_actions)
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
                        book_id=candidate.book_id,
                        payload=dict(candidate.payload),
                        score=max(candidate.score, bonus + self._limits.replan_feedback_bonus),
                        rationale="; ".join(part for part in [candidate.rationale, reason, "llm guidance"] if part),
                        expected_effect=patch.get("expected_effect") or candidate.expected_effect,
                        predicted_outcome=dict(candidate.predicted_outcome or {}),
                        metadata={**candidate.metadata, "llm_guidance": True, "llm_reason": reason},
                    )
                )
                continue
            updated.append(candidate)
        if not matched:
            updated.append(
                _CandidateRecord(
                    action_id=action_id,
                    book_id=action_id,
                    payload={},
                    score=max(self._limits.replan_feedback_bonus, bonus),
                    rationale=reason or f"llm suggested {action_id}",
                    expected_effect=patch.get("expected_effect"),
                    predicted_outcome={"kind": "grid_change", "confidence": float(bonus or 0.4)},
                    metadata={"book_id": action_id, "llm_guidance": True, "llm_reason": reason},
                )
            )
        return updated

    def _should_force_explore(self, state: WorkflowState, top_candidate: _CandidateRecord) -> bool:
        """Force exploration when the top candidate has been used too many consecutive times."""
        attempts = int(state.action_attempt_counts.get(top_candidate.book_id, 0))
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
    def _predicted_outcome(
        graph_record: Mapping[str, Any],
        graph_evidence: Mapping[str, Any],
        is_untested: bool,
    ) -> dict[str, Any]:
        raw = (graph_evidence or {}).get("raw") or {}
        recorded_kind = raw.get("effect_kind") or graph_record.get("effect_kind")
        if recorded_kind in ("grid_change", "no_change", "level_gain", "state_change"):
            confidence = float((graph_evidence or {}).get("confidence") or 0.5)
            return {"kind": str(recorded_kind), "confidence": confidence}
        if is_untested:
            return {"kind": "grid_change", "confidence": 0.3}
        return {"kind": "grid_change", "confidence": 0.4}

    @staticmethod
    def _click_targets(
        perception: PerceptionSnapshot,
        limit: int = 3,
        *,
        attempted_coords: Mapping[tuple[int, int], int] | None = None,
    ) -> list[dict[str, Any]]:
        """Rank click targets from perceived entities: small, distinct objects first.

        A151: `attempted_coords` maps (x, y) -> attempt_count for coordinates already
        proposed via `ACTION6@x,y` in this workflow, so repeated clicks on the same
        spot are penalized (not excluded) and rank behind fresh, unexplored targets.
        """
        color_counts: dict[str, int] = {}
        for entity in perception.entities:
            color_counts[entity.value] = color_counts.get(entity.value, 0) + 1

        scored: list[tuple[float, dict[str, Any]]] = []
        for entity in perception.entities:
            attrs = entity.attributes or {}
            coverage = float(attrs.get("coverage") or 0.0)
            if coverage > 0.5:
                continue
            cell_count = int(attrs.get("cell_count") or 0)
            if cell_count == 0:
                continue
            centroid = attrs.get("centroid") or (0, 0)
            row = int(round(float(centroid[0])))
            col = int(round(float(centroid[1])))
            x = max(0, min(63, col))
            y = max(0, min(63, row))

            rarity = 1.0 / (1.0 + float(color_counts.get(entity.value, 0)))
            salience = (1.0 / (1.0 + cell_count)) + rarity
            if entity.kind in ("point", "block"):
                salience += 0.2

            attempts_here = (attempted_coords or {}).get((x, y), 0)
            if attempts_here:
                # A151: push repeats behind fresh targets; does not floor at 0 so
                # relative ranking among repeats (fewest attempts first) is preserved.
                salience -= 0.5 * attempts_here

            scored.append(
                (
                    salience,
                    {
                        "x": x,
                        "y": y,
                        "entity_kind": entity.kind,
                        "entity_color": entity.value,
                    },
                )
            )

        scored.sort(key=lambda item: item[0], reverse=True)
        return [target for _, target in scored[:limit]]

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
            "book_id": candidate.book_id,
            "payload": dict(candidate.payload),
            "score": candidate.score,
            "rationale": candidate.rationale,
            "expected_effect": candidate.expected_effect,
            "predicted_outcome": dict(candidate.predicted_outcome or {}),
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
            payload=dict(candidate.payload),
            predicted_outcome=dict(candidate.predicted_outcome or {}),
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