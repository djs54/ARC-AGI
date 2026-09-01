"""ARC v2 goal resolution with deterministic, graph-backed, and optional LLM tiers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .mechanic_fusion import TransferredRuleRecord, fuse_transferred_rules
from .ports import GraphQueryPort, LLMMessage, LLMPort
from .rule_extraction import compute_fingerprint
from .types import GoalHypothesis, PerceivedEntity, PerceptionSnapshot, PhaseResult, PhaseStatus, ResolvedGoal, WorkflowPhase, WorkflowState

# A179: cross-game single-rule transfer is a lead, not a fact -- its
# contribution must be strictly smaller than the in-game
# entity_history:has_changed boost (0.08 flat, see _tier_one_hypotheses).
_TRANSFER_CONFIDENCE_MULTIPLIER = 0.05

# A186: a fused Mechanic (multiple transferred rules that structurally agree)
# is corroborating evidence *across* transferred rules, not stronger evidence
# than a single transfer match -- its multiplier is exactly half of the
# single-rule transfer multiplier above, and mechanic_fusion.py's merge
# policy additionally caps a fused Mechanic's confidence strictly below its
# strongest member's confidence. Together these guarantee
# 0 < mechanic_boost < transfer_boost whenever a confident fusion exists
# (see tests/test_a186_mechanic_fusion.py's ordering regression test).
_MECHANIC_FUSION_CONFIDENCE_MULTIPLIER = _TRANSFER_CONFIDENCE_MULTIPLIER / 2


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
        hypotheses = self._tier_one_hypotheses(state, perception, graph_port=graph_port)
        graph_evidence: list[dict[str, Any]] = []

        if graph_port is not None:
            hypotheses, graph_evidence = self._merge_graph_evidence(hypotheses, graph_port, perception)

        # A236: update the ambiguous-pair streak from the *same* hypothesis
        # ordering _should_escalate_to_llm is about to see (post graph-merge,
        # pre-LLM, pre-failure-decay/grounding-gate reordering below) --
        # must run every cycle that computes ordered hypotheses, not only
        # when escalation actually fires, so the streak tracks "how many
        # consecutive cycles has this pair been ambiguous," not "how many
        # times did we escalate." Runs before _should_escalate_to_llm so
        # that function sees this cycle's already-updated streak.
        self._update_ambiguous_pair_streak(state, hypotheses)

        llm_applied = False
        llm_reason: str | None = None
        if llm_port is not None and self._should_escalate_to_llm(state, hypotheses):
            llm_patch = self._query_llm(llm_port, perception, hypotheses)
            if llm_patch is not None:
                hypotheses = self._merge_llm_patch(hypotheses, llm_patch)
                llm_applied = True
                llm_reason = llm_patch.get("reason")

                # A236 (reopened 2026-09-01): an escalation actually fired
                # this cycle -- reset the per-pair streak so the NEXT
                # escalation on this same pair waits a fresh
                # llm_patience_steps cycles, instead of the streak (which
                # _update_ambiguous_pair_streak increments unconditionally
                # every cycle the pair stays unchanged) monotonically
                # growing past the threshold and staying there forever.
                # That was the reopened bug: the gate suppressed exactly
                # one cycle total, then escalated every cycle for the rest
                # of the pair's life, because nothing ever brought the
                # streak back below llm_patience_steps once it crossed.
                #
                # Reset unconditionally on any successful escalation
                # (whether `ambiguous` or `under_confident` was the
                # deciding disjunct in _should_escalate_to_llm), not only
                # an `ambiguous`-triggered one: _query_llm sends the full
                # ordered candidate list every time it is called, and the
                # returned patch is merged into hypotheses regardless of
                # which condition triggered the call -- the LLM call
                # itself does not know or care why it was invoked, so it
                # provides equally fresh evidence about the current
                # top-two pair either way. Gating the reset on
                # `llm_applied` (not merely "escalation was requested")
                # is deliberate too: a query that returned an unparseable
                # response (llm_patch is None) paid the wall-clock cost
                # but produced no new evidence, so the pair's patience
                # streak should not be reset in that case -- this mirrors
                # `llm_escalated` in the metadata below, which is also
                # keyed off `llm_applied`, not off escalation having been
                # merely attempted.
                state.ambiguous_pair_streak = 0

        hypotheses = self._order_hypotheses(hypotheses)
        hypotheses = self._apply_failure_decay(state, hypotheses)
        hypotheses = self._order_hypotheses(hypotheses)
        hypotheses, grounding_gate_passed = self._apply_grounding_gate(state, perception, hypotheses, graph_evidence)

        # A203: apply anchor hint to reorder hypotheses if a goal-type hint exists
        anchor_hint = getattr(state, "annatar_anchor_hint", None)
        if anchor_hint is not None and anchor_hint.anchor_type == "goal" and anchor_hint.decision in ("repeat_deepen", "repeat_retry"):
            anchored = next((h for h in hypotheses if h.goal_id == anchor_hint.anchor_ref), None)
            if anchored is not None and anchored is not hypotheses[0]:
                hypotheses = [anchored] + [h for h in hypotheses if h is not anchored]

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

    def _tier_one_hypotheses(
        self,
        state: WorkflowState,
        perception: PerceptionSnapshot,
        *,
        graph_port: GraphQueryPort | None = None,
    ) -> list[GoalHypothesis]:
        hypotheses: list[GoalHypothesis] = []

        # A171: rank by distinctiveness (rare color + small relative size)
        # instead of raw raster-scan order -- entities[:3] used to mean "the
        # first three same-colored blobs the scan happened to encounter,"
        # which on a puzzle with dozens/hundreds of entities meant the real
        # goal-relevant entity could never earn a tier-1 hypothesis at all.
        # Mirrors plan_generator.py::_click_targets' own color_counts-based
        # rarity philosophy, just applied to goal selection instead of click
        # targets.
        color_counts: dict[str, int] = {}
        for entity in perception.entities:
            color_counts[entity.value] = color_counts.get(entity.value, 0) + 1

        ranked_entities = sorted(
            perception.entities,
            key=lambda e: self._distinctiveness_score(e, color_counts),
            reverse=True,
        )[:3]

        for index, entity in enumerate(ranked_entities):
            goal_id = self._slugify(f"{entity.kind}-{entity.value or index}")
            description = self._describe_entity_goal(entity.kind, entity.value, perception)
            score = self._distinctiveness_score(entity, color_counts)
            confidence = min(0.75, self._limits.min_heuristic_confidence + 0.12 + (0.05 * min(score, 1.0)))
            evidence = [f"entity:{entity.kind}:{entity.value}"]

            # A176: consume A175/A176's persisted transition history -- an
            # entity with a real history of changing is meaningfully more
            # likely to be a goal-relevant object than one that's never been
            # observed to change. Boost, don't replace, the heuristic score.
            entity_ref = entity.attributes.get("entity_ref")
            if graph_port is not None and entity_ref is not None:
                fetch_history = getattr(graph_port, "fetch_entity_history", None)
                if fetch_history is not None:
                    try:
                        history = fetch_history(entity_ref)
                    except Exception:
                        history = {}
                    if isinstance(history, Mapping) and history.get("changed_count_total", 0) > 0:
                        confidence = min(0.75, confidence + 0.08)
                        evidence.append("entity_history:has_changed")

                        # A179: cross-game transfer -- fingerprint the entity's
                        # most recent observed transition (action_family +
                        # color-invariant magnitude bucket) and check whether
                        # structurally similar mechanics elsewhere tend to be
                        # meaningful. Deliberately a much smaller boost than
                        # in-game evidence above -- transfer is a lead, not a
                        # fact, and must not be trusted as much as a
                        # confirmed-in-this-game observation.
                        transitions = history.get("transitions") or []
                        fetch_transferred = getattr(graph_port, "fetch_transferred_rules", None)
                        if fetch_transferred is not None and transitions:
                            latest = transitions[-1] if isinstance(transitions[-1], Mapping) else {}
                            transfer_action_id = latest.get("action_id")
                            if transfer_action_id:
                                transfer_changed_count = latest.get("changed_count", history.get("changed_count_total", 0))
                                fingerprint = compute_fingerprint(str(transfer_action_id), int(transfer_changed_count or 0))
                                try:
                                    transferred = fetch_transferred(fingerprint.key())
                                except Exception:
                                    transferred = []
                                if transferred:
                                    best_transfer_confidence = max((r.get("confidence", 0.0) for r in transferred), default=0.0)
                                    confidence = min(0.75, confidence + best_transfer_confidence * _TRANSFER_CONFIDENCE_MULTIPLIER)
                                    evidence.append("entity_history:transfer_match")

                                    # A186: do the transferred rules for this
                                    # fingerprint actually agree with each
                                    # other (shared preconditions), not just
                                    # share a fingerprint? A179's own review
                                    # found the fingerprint alone can collide
                                    # on unrelated mechanics. A confident
                                    # fusion is corroboration across multiple
                                    # transferred rules, so its boost is
                                    # additional but deliberately smaller than
                                    # the single-rule transfer boost above.
                                    confidence, mechanic_matched = self._apply_mechanic_fusion_boost(
                                        confidence, transferred, fingerprint.key(), graph_port
                                    )
                                    if mechanic_matched:
                                        evidence.append("entity_history:mechanic_fusion")

            hypotheses.append(
                GoalHypothesis(
                    goal_id=goal_id,
                    description=description,
                    confidence=confidence,
                    evidence=tuple(evidence),
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

    @staticmethod
    def _distinctiveness_score(entity: PerceivedEntity, color_counts: Mapping[str, int]) -> float:
        """A171: rare-colored, small-relative-size entities score higher --
        the same "prefer small, distinct objects" principle
        plan_generator.py::_click_targets already uses for click-target
        ranking, ported here for goal selection."""
        rarity = 1.0 / max(color_counts.get(entity.value, 1), 1)
        smallness = 1.0 - min(entity.attributes.get("coverage", 0.0), 1.0)
        return (0.6 * rarity) + (0.4 * smallness)

    @staticmethod
    def _apply_mechanic_fusion_boost(
        confidence: float,
        transferred: Sequence[Mapping[str, Any]],
        fingerprint_key: str,
        graph_port: GraphQueryPort | None,
    ) -> tuple[float, bool]:
        """A186: block+match+merge the transferred rules already fetched for
        this fingerprint (fuse_transferred_rules is pure, so re-running it on
        an already-fetched, single-fingerprint list is cheap -- no extra
        network call). Applies the smaller mechanic-fusion boost only when a
        confident fusion exists, and opportunistically persists the fusion
        result when the graph port supports it. Never raises: a missing
        record_mechanic_fusion method or a write failure only skips
        persistence, it never blocks the confidence boost already computed."""
        records = tuple(
            TransferredRuleRecord(
                rule_id=str(rule.get("rule_id")),
                confidence=float(rule.get("confidence", 0.0) or 0.0),
                source_game_id=rule.get("source_game_id"),
                fingerprint=str(rule.get("fingerprint", fingerprint_key)),
                preconditions=tuple(rule.get("preconditions") or ()),
            )
            for rule in transferred
            if isinstance(rule, Mapping) and rule.get("rule_id")
        )
        fusions = fuse_transferred_rules(records)
        if not fusions:
            return confidence, False

        best_fusion = max(fusions, key=lambda fusion: fusion.confidence)
        boosted = min(0.75, confidence + best_fusion.confidence * _MECHANIC_FUSION_CONFIDENCE_MULTIPLIER)

        record_fusion = getattr(graph_port, "record_mechanic_fusion", None) if graph_port is not None else None
        if record_fusion is not None:
            try:
                record_fusion(best_fusion)
            except Exception:
                pass

        return boosted, True

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

    def _update_ambiguous_pair_streak(self, state: WorkflowState, hypotheses: Sequence[GoalHypothesis]) -> None:
        """A236: track how many consecutive cycles the same top-two goal_id
        pair has been ambiguous, so _should_escalate_to_llm's `ambiguous`
        branch can suppress redundant re-escalation on an unchanged
        question. Deliberately separate from consecutive_no_progress_count
        (see WorkflowState's field comment) and deliberately a side effect
        confined to this dedicated method -- _should_escalate_to_llm itself
        stays a pure predicate, matching its existing shape."""
        if len(hypotheses) < 2:
            state.last_ambiguous_pair = None
            state.ambiguous_pair_streak = 0
            return

        ordered = self._order_hypotheses(hypotheses)
        top = ordered[0]
        runner_up = ordered[1]
        pair = (top.goal_id, runner_up.goal_id)
        ambiguous_raw = (top.confidence - runner_up.confidence) <= self._limits.ambiguity_gap

        if not ambiguous_raw:
            # Real evidence moved the pair apart -- any future re-ambiguity
            # (same pair or not) must be treated as new, not still-suppressed.
            state.last_ambiguous_pair = None
            state.ambiguous_pair_streak = 0
        elif state.last_ambiguous_pair == pair:
            state.ambiguous_pair_streak += 1
        else:
            state.last_ambiguous_pair = pair
            state.ambiguous_pair_streak = 0

    def _should_escalate_to_llm(self, state: WorkflowState, hypotheses: Sequence[GoalHypothesis]) -> bool:
        if len(hypotheses) < 2:
            return bool(hypotheses and hypotheses[0].confidence < self._limits.low_confidence_threshold and state.consecutive_no_progress_count >= self._limits.llm_patience_steps)

        ordered = self._order_hypotheses(hypotheses)
        top = ordered[0]
        runner_up = ordered[1]
        pair = (top.goal_id, runner_up.goal_id)
        ambiguous_raw = (top.confidence - runner_up.confidence) <= self._limits.ambiguity_gap
        if ambiguous_raw and state.last_ambiguous_pair == pair and state.ambiguous_pair_streak > 0:
            # A236: this exact pair was already ambiguous last cycle with no
            # new evidence (streak > 0, tracked by _update_ambiguous_pair_streak)
            # -- keep suppressing re-escalation until the streak crosses the
            # same llm_patience_steps threshold the under_confident branch
            # below already uses, mirroring its existing precedent.
            ambiguous = state.ambiguous_pair_streak >= self._limits.llm_patience_steps
        else:
            # Either genuinely not ambiguous, or this is the first cycle
            # this exact pair has been ambiguous (streak == 0) -- escalate
            # immediately, preserving today's responsiveness to new ambiguity.
            ambiguous = ambiguous_raw
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
                        "grid_text": perception.metadata.get("grid_text", "") if isinstance(perception.metadata, Mapping) else "",
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
        # A224: an LLM-proposed goal_id that doesn't match any presented,
        # graph-derived hypothesis used to be appended as a brand-new
        # hypothesis with zero graph evidence -- the LLM escaping the
        # graph's bound entirely. Confirmed nothing downstream would have
        # caught this: _apply_grounding_gate is a pass-through whenever
        # state.active_goal is None (the cold-start case this whole
        # investigation started from), and _order_hypotheses ranks purely
        # by confidence, which the LLM sets itself -- so an ungrounded
        # invention could become `selected` outright. Dropped, not kept-
        # but-downgraded: confidence alone doesn't gate selection here, so
        # a "downgraded" hypothesis would still need an explicit filter
        # elsewhere to be safe, which doesn't exist. The LLM's vote only
        # counts if it picked from what the graph actually offered.
        goal_id = patch.get("goal_id")
        confidence = float(patch.get("confidence", 0.0) or 0.0)
        reason = str(patch.get("reason", "") or "")
        evidence = tuple(str(item) for item in patch.get("evidence", ()) if item is not None)
        updated: list[GoalHypothesis] = []
        for hypothesis in hypotheses:
            if goal_id and hypothesis.goal_id == goal_id:
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
        graph_evidence: Sequence[Mapping[str, Any]] = (),
    ) -> tuple[list[GoalHypothesis], bool]:
        if state.active_goal is None:
            return hypotheses, True

        progress_made = self._observed_progress(state, perception)
        if progress_made:
            return hypotheses, True

        active_goal_id = state.active_goal.selected.goal_id
        ceiling = state.active_goal.selected.confidence

        # A233: the local grid-hash comparison above answers "did the grid
        # visibly change" -- cheap, and it catches a real case the graph
        # can't (a visible change is trustworthy on its own, no round trip
        # needed). But it can't see the deeper case: the grid looks the
        # same, yet the graph has *reasons* -- real evidence, not just
        # local drift -- to think this goal is still (or no longer) worth
        # pursuing. Consult the graph evidence `_merge_graph_evidence`
        # already fetched earlier THIS SAME resolve() call (zero extra
        # round trip -- this function runs every cycle a goal stays active,
        # so a fresh fetch here every cycle was rejected as unaffordable;
        # see backlog/A233.md's Outcome for the reasoning). Only a record
        # whose goal_id exactly matches the active goal counts -- most
        # graph_evidence records today carry a synthetic source-derived
        # goal_id ("mechanic_priors", "goal_evidence", ...) that can't
        # collide with a real entity-derived hypothesis id, so this is a
        # no-op (falls through to the pre-A233 local-only behavior) unless
        # the graph genuinely has an opinion on this specific goal.
        graph_record = next(
            (record for record in graph_evidence if record.get("goal_id") == active_goal_id),
            None,
        )
        graph_confidence = float(graph_record.get("confidence", 0.0) or 0.0) if graph_record is not None else None

        if graph_confidence is not None and graph_confidence >= ceiling:
            # The graph itself still backs this goal at least as strongly as
            # its last-known ceiling, despite no visible grid change this
            # cycle -- trust the graph's fresher, evidence-backed opinion
            # over the local heuristic and don't clamp at all.
            return hypotheses, True

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
                effective_ceiling = ceiling
                gate_reason = "clamped"
                if graph_confidence is not None and graph_confidence < ceiling:
                    # The graph actively contradicts continuing this goal (its
                    # own confidence has fallen below the local ceiling) --
                    # clamp to the graph's lower figure, not just the stale
                    # local ceiling, so real negative graph evidence actually
                    # bites instead of being silently outvoted by local drift.
                    effective_ceiling = min(ceiling, graph_confidence)
                    gate_reason = "clamped_graph_contradicted"
                clamped.append(
                    GoalHypothesis(
                        goal_id=hypothesis.goal_id,
                        description=hypothesis.description,
                        confidence=effective_ceiling,
                        evidence=hypothesis.evidence,
                        metadata=self._merge_metadata(hypothesis.metadata, {"grounding_gate": gate_reason, "grounding_ceiling": effective_ceiling}),
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