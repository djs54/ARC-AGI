"""ARC v2 graph-query adapter over the landed B278 MCP tool surface."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .mechanic_fusion import MechanicFusionResult
from .rule_extraction import compute_fingerprint, entity_preconditions, extract_candidate_signatures
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
    # A176: persist A170's before/after diffs as graph State Nodes instead of
    # a one-shot prompt fragment. B309 landed these server-side as generic
    # (non-arc-prefixed) tools -- Transition/Rule aren't ARC-specific concepts,
    # so hippocampy exposes them under general names any client can call.
    "record_transition": "record_transition",
    "fetch_entity_history": "get_entity_history",
    # A177: causal rules as first-class graph objects (PREDICTS/FALSIFIED_BY),
    # replacing per-action tally counters. Landed server-side in B309.
    "record_rule_evidence": "record_rule",
    "fetch_rules_for_action": "get_rules_for_action",
    # A192: entity-neighborhood evidence -- live hypotheses/mechanics the graph
    # associates with a specific entity, the entity-scoped analog of fetch_rules_for_action.
    "fetch_entity_neighborhood": "arc_get_entity_neighborhood",
    # A179: cross-game rule transfer by structural (color-invariant)
    # fingerprint. Landed server-side in B309.
    "fetch_transferred_rules": "get_transferred_rules",
    # A186: fuse multiple structurally-matched transferred rules into one
    # aggregate Mechanic record. Not yet implemented server-side -- see
    # docs/handoff/B278-mechanic-fusion.md. Client sends/reads these
    # defensively; capability_missing degrades to a clean no-op/[] like
    # every other pre-launch tool in this table.
    "record_mechanic_fusion": "record_mechanic",
    "fetch_mechanic_candidates": "get_mechanic_candidates",
    # A201: investigation-thread state durability for the trajectory Annatar
    # (docs/superpowers/specs/2026-08-23-trajectory-reasoner-design.md). Four
    # new tools for managing Annatar's durable decisions, resumes, and
    # cycle tracking. Not yet server-side -- see docs/handoff/B278-investigation-
    # thread-schema.md. Clients degrade cleanly to defined empty/no-op results
    # on capability_missing.
    "start_or_resume_thread": "arc_start_or_resume_thread",
    "write_thread_state": "arc_write_thread_state",
    "write_cycle": "arc_write_cycle",
    "confirm_cycle": "arc_confirm_cycle",
}


@dataclass(slots=True)
class ArcGraphQueryPort:
    """Sync adapter that maps ARC v2 graph calls to concrete B278 tool names."""

    brain_client: Any
    task_id: str
    session_id: str
    strict: bool = True
    tool_names: Mapping[str, str] = field(default_factory=lambda: dict(ARC_V2_TOOL_NAMES))
    _capability_missing_count: int = field(default=0)
    _hypothesis_confirm_contradict_count: int = field(default=0)
    _goal_confidence_write_count: int = field(default=0)

    def ingest_perception(self, perception: PerceptionSnapshot) -> dict[str, Any]:
        # A221 Finding 2: perception.metadata["disappeared_entities"]
        # (A219, plain-dict PerceivedEntity.to_dict() shape) reconstructed
        # and serialized with the exact same fidelity as visible entities --
        # a vanished entity is still a real perceived fact, not degraded
        # telemetry. Sent as an additional field on the existing
        # ingest_perception call (one atomic per-step write, matching how
        # loop_signal/repeated_grid_count are already bundled into `effect`
        # rather than a second round-trip) -- degrades gracefully if the
        # server doesn't understand the new field yet (same non-strict-MCP
        # pattern as every other graph consumer here); the server-side work
        # to actually act on it is tracked separately, not guessed at here.
        disappeared_raw = perception.metadata.get("disappeared_entities") or []
        disappeared_entities = [
            self._serialize_entity(PerceivedEntity.from_dict(d)) for d in disappeared_raw
        ]
        payload = {
            "task_id": self.task_id,
            "step": self._perception_step(perception),
            "grid_hash": perception.grid_hash,
            "entities": [self._serialize_entity(entity) for entity in perception.entities],
            "disappeared_entities": disappeared_entities,
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
                "attempts": int(result.get("attempts") or result.get("attempt_count") or result.get("evidence_count") or result.get("steps_used") or 0),
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

    def fetch_entity_history(self, entity_ref: Any) -> dict[str, Any]:
        """A176: what has happened to this entity across the game so far --
        the consumer query for A176's persisted Transition nodes."""
        result = self._call_tool("fetch_entity_history", {"task_id": self.task_id, "entity_ref": entity_ref})
        if isinstance(result, Mapping):
            if result.get("status") == "capability_missing":
                return {"transitions": [], "changed_count_total": 0}
            transitions = result.get("transitions", [])
            return {
                "transitions": list(transitions) if isinstance(transitions, (list, tuple)) else [],
                "changed_count_total": int(result.get("changed_count_total", 0) or 0),
            }
        return {"transitions": [], "changed_count_total": 0}

    def fetch_rules_for_action(self, action_id: str) -> list[dict[str, Any]]:
        """A177: live (unfalsified) rules relevant to an action -- the
        consumer query for A177's Rule/PREDICTS/FALSIFIED_BY graph objects,
        replacing the flat falsification_penalty counter with real causal
        claims plan_generator.py can weigh."""
        result = self._call_tool("fetch_rules_for_action", {"task_id": self.task_id, "action_id": action_id})
        if not isinstance(result, Mapping) or result.get("status") == "capability_missing":
            return []
        rules = result.get("rules", [])
        if not isinstance(rules, (list, tuple)):
            return []
        return [
            {
                "rule_id": rule.get("rule_id"),
                "from_color": rule.get("from_color"),
                "to_color": rule.get("to_color"),
                "confidence": float(rule.get("confidence", 0.0) or 0.0),
                "falsified": bool(rule.get("falsified", False)),
            }
            for rule in rules
            if isinstance(rule, Mapping)
        ]

    def fetch_entity_neighborhood(self, entity_ref: Any) -> dict[str, Any]:
        """A192: live hypotheses/mechanics the graph already associates with a
        specific entity -- the neighborhood-inspection step of the graph-guided
        investigation loop, entity-scoped rather than action-family-scoped.
        B359 follow-up (2026-08-23): also carries "rules" -- live (unfalsified)
        Rule nodes linked via a separate ENTITY_RULE edge, kept apart from
        "hypotheses" (ENTITY_HYPOTHESIS) since a confirmed causal rule and a
        standing hypothesis under test are different epistemic states, not
        the same thing under two names."""
        result = self._call_tool("fetch_entity_neighborhood", {"task_id": self.task_id, "entity_ref": entity_ref})
        if not isinstance(result, Mapping) or result.get("status") == "capability_missing":
            return {"hypotheses": [], "rules": [], "mechanics": []}
        hypotheses = result.get("hypotheses", [])
        rules = result.get("rules", [])
        mechanics = result.get("mechanics", [])
        return {
            "hypotheses": list(hypotheses) if isinstance(hypotheses, (list, tuple)) else [],
            "rules": list(rules) if isinstance(rules, (list, tuple)) else [],
            "mechanics": list(mechanics) if isinstance(mechanics, (list, tuple)) else [],
        }

    def start_or_resume_thread(self, anchor_ref: Any, anchor_type: str) -> dict[str, Any]:
        """Investigation-thread lookup/create -- resume support for the
        trajectory Annatar (see docs/superpowers/specs/2026-08-23-trajectory-
        reasoner-design.md). Degrades to a fresh-thread-shaped result when the
        server capability doesn't exist yet."""
        result = self._call_tool(
            "start_or_resume_thread",
            {"task_id": self.task_id, "anchor_ref": anchor_ref, "anchor_type": anchor_type},
        )
        if not isinstance(result, Mapping) or result.get("status") == "capability_missing":
            return {"thread_id": None, "state": "exploring", "resumed": False, "last_cycle": None}
        return {
            "thread_id": result.get("thread_id"),
            "state": str(result.get("state", "exploring")),
            "resumed": bool(result.get("resumed", False)),
            "last_cycle": result.get("last_cycle"),
        }

    def write_thread_state(self, thread_id: Any, state: str) -> dict[str, Any]:
        """Durable write of Annatar's resolved state. No-op (not an error)
        when thread_id is None -- callers pass None when start_or_resume_thread
        itself degraded, and this must not raise in that case."""
        if thread_id is None:
            return {"status": "skipped", "reason": "no_thread_id"}
        result = self._call_tool("write_thread_state", {"thread_id": thread_id, "state": state})
        return self._normalize_write_result(result, tool_key="write_thread_state")

    def write_cycle(self, thread_id: Any, step: int, action_sent: bool) -> dict[str, Any]:
        """Write-ahead call -- must be invoked BEFORE the real API action is
        sent, per spec section 7's write-intent-first invariant. No-op when
        thread_id is None."""
        if thread_id is None:
            return {"cycle_id": None}
        result = self._call_tool("write_cycle", {"thread_id": thread_id, "step": step, "action_sent": action_sent})
        if not isinstance(result, Mapping) or result.get("status") == "capability_missing":
            return {"cycle_id": None}
        return {"cycle_id": result.get("cycle_id")}

    def confirm_cycle(self, cycle_id: Any, decision: str, confirmed: bool) -> dict[str, Any]:
        """Post-action (or resume-time reconciliation) confirmation write.
        No-op when cycle_id is None."""
        if cycle_id is None:
            return {"status": "skipped", "reason": "no_cycle_id"}
        result = self._call_tool("confirm_cycle", {"cycle_id": cycle_id, "decision": decision, "confirmed": confirmed})
        return self._normalize_write_result(result, tool_key="confirm_cycle")

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
        payload: dict[str, Any] = {
            "task_id": self.task_id,
            "hypothesis_id": candidate_id,
            "evidence": {"reason": vet.reason, "approved": vet.approved, "metadata": dict(vet.metadata)},
        }
        # B359: pass entity_ref through so the server merges an
        # ENTITY_HYPOTHESIS edge (GridEntity -> Hypothesis) with this
        # confirm/contradict call -- the write-side counterpart to A192's
        # fetch_entity_neighborhood read. Only click-target candidates carry
        # entity_ref in metadata (A192); non-click actions are unaffected.
        entity_ref = self._vet_entity_ref(vet)
        if entity_ref is not None:
            payload["entity_ref"] = entity_ref
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

    def record_transition(
        self,
        execution: ExecutionResult,
        grid_diff: Mapping[str, Any],
        entities: Sequence[Any] = (),
    ) -> dict[str, Any]:
        """A176: persist A170's before/after cell diff as a graph State Node,
        summarized as a bounded color-transition histogram (not per-cell --
        see backlog/A176.md Step 0) and attributed to the A175 entity_ref
        whose bbox contains the most changed cells, when determinable.

        A213: when changed_cells is empty (no-op action), still send a minimal
        record with empty color_transitions to distinguish "tried, zero effect"
        from "never attempted" (cf. fetch_rules_for_action, fetch_causal_path,
        fetch_entity_neighborhood). Server accepts empty color_transitions.

        A218: on that same no-op path, entity_ref used to be hardcoded None --
        _attribute_entity's only attribution mechanism is bbox-overlap-with-
        changed-cells, which is empty by definition when there's nothing to
        attribute. But for a click-shaped action (ACTION6), the ARC side
        already knows exactly which entity was targeted (plan_generator.py
        stamps entity_ref onto the click candidate's metadata) independent of
        whether anything visibly changed. Falling back to that known target
        on the no-op path means a repeatedly-clicked, confirmed-inert entity
        accumulates real (changed_count=0) Transition history keyed to its
        own entity_ref instead of "none" -- the fact
        fetch_entity_history(entity_ref) needs to ever report anything for
        it. See backlog/A218.md's Outcome for the live-graph evidence this
        closes.

        A229: on the real-change path, _attribute_entity is now gated by the
        click coordinate that caused this step (when known) -- an entity is
        only eligible for the "most changed cells" tiebreak if its own bbox
        actually contains the click. Unnormalized bbox-overlap alone let any
        entity whose bbox spans a large fraction of the grid structurally
        absorb credit for changes anywhere on the board, independent of
        whether a click on it caused anything (see backlog/A229.md's live
        evidence: a background-sized entity credited for 3/3 real
        transitions in a 30-step episode, every time the click landed
        outside its own bbox). When no entity's bbox contains the click,
        this falls back to A218's _targeted_entity_ref -- the same "we know
        what was clicked" signal already used on the no-op path below,
        extended here to the one real-change case bbox-overlap alone can't
        resolve."""
        changed_cells = grid_diff.get("changed_cells") if isinstance(grid_diff, Mapping) else None

        if changed_cells:
            color_transitions = self._summarize_color_transitions(changed_cells)
            changed_count = int(grid_diff.get("changed_count", len(changed_cells)) or 0)
            entity_ref = self._attribute_entity_for_execution(execution, changed_cells, entities)
        else:
            # A213: no-op action — send minimal record marking "tried, zero effect"
            color_transitions = []
            changed_count = 0
            # A218: attribute to the action's own known target when bbox
            # attribution has nothing to work with.
            entity_ref = self._targeted_entity_ref(execution)

        payload = {
            "task_id": self.task_id,
            "step": self._execution_step(execution),
            "action_id": execution.action_id,
            "changed_count": changed_count,
            "color_transitions": color_transitions,
            "entity_ref": entity_ref,
        }
        return self._normalize_write_result(self._call_tool("record_transition", payload), tool_key="record_transition")

    def record_rule_evidence(
        self,
        execution: ExecutionResult,
        grid_diff: Mapping[str, Any],
        entities: Sequence[Any] = (),
    ) -> dict[str, Any]:
        """A177: extract candidate rule signatures (deterministic, see
        rule_extraction.py) from this step's observed color-transition
        histogram and send them for the server to confirm/falsify existing
        Rule nodes against, or create new ones.

        A213 (investigated, not applied here): a no-op-signal branch mirroring
        record_transition's was tried but reverted on review -- hippocampy's
        record_rule (campy/brain/thalamus/tools/arc_queries.py) loops over
        candidate_signatures and writes nothing when the list is empty
        (`for sig in signatures: ...` with signatures=[] is a true no-op,
        unlike record_transition's unconditional Transition-node MERGE). A
        Rule is inherently a from_color->to_color transition; there is no
        natural "null rule" to write when nothing changed, and sending an
        empty-signatures call is genuinely indistinguishable server-side from
        never calling it at all -- it would just be a wasted round-trip, not
        an information gain. record_transition's own no-op record (below)
        already closes the "tried, zero effect" visibility gap this card
        cared about; a real "no-op Rule" would need a hippocampy schema
        change, left as a possible future ask rather than built speculatively
        here (see backlog/A213.md's Outcome)."""
        changed_cells = grid_diff.get("changed_cells") if isinstance(grid_diff, Mapping) else None
        if not changed_cells:
            return {"status": "no_changes", "recorded": False}

        color_transitions = self._summarize_color_transitions(changed_cells)
        signatures = extract_candidate_signatures(execution.action_id, color_transitions)
        if not signatures:
            return {"status": "no_changes", "recorded": False}

        entity_ref = self._attribute_entity_for_execution(execution, changed_cells, entities)
        payload: dict[str, Any] = {
            "task_id": self.task_id,
            "step": self._execution_step(execution),
            "action_id": execution.action_id,
            "candidate_signatures": [
                {"action_family": sig.action_family, "from_color": sig.from_color, "to_color": sig.to_color}
                for sig in signatures
            ],
            # A179: color-invariant structural fingerprint for cross-game
            # transfer -- indexed server-side so get_transferred_rules
            # can retrieve rules by mechanic shape (action_family + change
            # magnitude), not by literal, non-transferable color values.
            "fingerprint": compute_fingerprint(
                execution.action_id,
                int(grid_diff.get("changed_count", len(changed_cells)) or 0),
            ).key(),
            # A186: palette-invariant feature tags describing the entity this
            # rule fired on, for cross-game structure-layer matching in
            # mechanic_fusion.py. Not yet stored/returned by hippocampy (see
            # docs/handoff/B278-mechanic-fusion.md); sending it now is
            # forward-compatible and harmless if the server ignores it.
            "preconditions": self._preconditions_for_entity(entities, entity_ref),
        }
        # B359 follow-up (2026-08-23): surface entity_ref at the top level too,
        # not just buried in preconditions tags -- record_rule now merges an
        # ENTITY_RULE (GridEntity -> Rule) edge when present, which is what
        # fetch_entity_neighborhood's "rules" key reads back.
        if entity_ref is not None:
            payload["entity_ref"] = entity_ref
        return self._normalize_write_result(self._call_tool("record_rule_evidence", payload), tool_key="record_rule_evidence")

    def fetch_transferred_rules(self, fingerprint_key: str) -> list[dict[str, Any]]:
        """A179: rules from *other* games whose structural fingerprint
        matches -- cross-game transfer, deliberately separate from
        fetch_rules_for_action's in-game-only scope (A164 game_id
        boundaries apply on the server side: this searches other games,
        not the current one)."""
        result = self._call_tool("fetch_transferred_rules", {"task_id": self.task_id, "fingerprint": fingerprint_key})
        if not isinstance(result, Mapping) or result.get("status") == "capability_missing":
            return []
        rules = result.get("rules", [])
        if not isinstance(rules, (list, tuple)):
            return []
        parsed: list[dict[str, Any]] = []
        for rule in rules:
            if not isinstance(rule, Mapping):
                continue
            preconditions = rule.get("preconditions")
            parsed.append(
                {
                    "rule_id": rule.get("rule_id"),
                    "confidence": float(rule.get("confidence", 0.0) or 0.0),
                    "source_game_id": rule.get("source_game_id"),
                    # A186: the query key is already the fingerprint every
                    # returned rule matched on -- fill it in client-side
                    # rather than wait on the server to echo it back.
                    "fingerprint": fingerprint_key,
                    # A186: precondition feature tags for structure-layer
                    # matching (mechanic_fusion.py). Defaults to empty until
                    # hippocampy stores/returns it -- see
                    # docs/handoff/B278-mechanic-fusion.md. An empty tuple
                    # can never reach the shared-feature threshold, so this
                    # degrades to "no confident match," never a false merge.
                    "preconditions": tuple(preconditions) if isinstance(preconditions, (list, tuple)) else (),
                }
            )
        return parsed

    def record_mechanic_fusion(self, fusion: MechanicFusionResult) -> dict[str, Any]:
        """A186: persist a deterministic fusion of 2+ structurally-matched
        transferred rules as one aggregate Mechanic record. The merge policy
        (mechanic_fusion.py) already ran client-side; this call is a pure
        write of its result."""
        payload = {
            "task_id": self.task_id,
            "fingerprint": fusion.fingerprint,
            "member_rule_ids": list(fusion.member_rule_ids),
            "source_game_ids": list(fusion.source_game_ids),
            "confidence": fusion.confidence,
            "merged_from": list(fusion.merged_from),
        }
        return self._normalize_write_result(self._call_tool("record_mechanic_fusion", payload), tool_key="record_mechanic_fusion")

    def fetch_mechanic_candidates(self, fingerprint_key: str) -> list[dict[str, Any]]:
        """A186: previously-fused Mechanic records for this fingerprint, if
        any. Degrades to [] on capability_missing or a malformed response,
        matching fetch_transferred_rules's shape."""
        result = self._call_tool("fetch_mechanic_candidates", {"task_id": self.task_id, "fingerprint": fingerprint_key})
        if not isinstance(result, Mapping) or result.get("status") == "capability_missing":
            return []
        mechanics = result.get("mechanics", [])
        if not isinstance(mechanics, (list, tuple)):
            return []
        return [
            {
                "mechanic_id": mechanic.get("mechanic_id"),
                "confidence": float(mechanic.get("confidence", 0.0) or 0.0),
                "member_rule_ids": list(mechanic.get("member_rule_ids", []) or []),
            }
            for mechanic in mechanics
            if isinstance(mechanic, Mapping)
        ]

    def pop_capability_missing_count(self) -> int:
        """Returns the capability_missing count accumulated since the last call,
        then resets it -- gives telemetry a natural per-step delta with no
        bookkeeping needed on the telemetry side."""
        count = self._capability_missing_count
        self._capability_missing_count = 0
        return count

    def pop_hypothesis_confirm_contradict_count(self) -> int:
        """Returns the hypothesis_confirm_contradict call count accumulated since the last call,
        then resets it."""
        count = self._hypothesis_confirm_contradict_count
        self._hypothesis_confirm_contradict_count = 0
        return count

    def pop_goal_confidence_write_count(self) -> int:
        """Returns the goal_confidence_write call count accumulated since the last call,
        then resets it."""
        count = self._goal_confidence_write_count
        self._goal_confidence_write_count = 0
        return count

    @staticmethod
    def _preconditions_for_entity(entities: Sequence[Any], entity_ref: Any) -> list[str]:
        """A186: locate the entity _attribute_entity resolved (if any) and
        derive its precondition feature tags. No entity attributed (or no
        matching record found) -> empty list, the same safe default
        fetch_transferred_rules already treats as never a confident match."""
        if entity_ref is None:
            return []
        for entity in entities:
            attributes = getattr(entity, "attributes", None)
            if isinstance(attributes, Mapping) and attributes.get("entity_ref") == entity_ref:
                return entity_preconditions(
                    getattr(entity, "kind", "unknown"),
                    attributes.get("cell_count"),
                    attributes.get("bbox"),
                )
        return []

    @staticmethod
    def _summarize_color_transitions(changed_cells: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        histogram: dict[tuple[Any, Any], int] = {}
        for cell in changed_cells:
            key = (cell.get("from"), cell.get("to"))
            histogram[key] = histogram.get(key, 0) + 1
        return [{"from": frm, "to": to, "count": count} for (frm, to), count in histogram.items()]

    @staticmethod
    def _attribute_entity(
        changed_cells: Sequence[Mapping[str, Any]],
        entities: Sequence[Any],
        click_row_col: tuple[int, int] | None = None,
    ) -> Any:
        """Attribute a step's changed cells to whichever perceived entity's
        bbox contains the most of them.

        A229: when the click coordinate that caused this step is known
        (``click_row_col``), an entity is only eligible for the "most
        changed cells" tiebreak if its own bbox actually contains that
        click -- otherwise an entity whose bbox spans a large fraction of
        the grid structurally absorbs credit for scattered changes anywhere
        on the board, regardless of whether a click on it caused anything
        (see backlog/A229.md's live evidence). Among entities whose bbox
        does contain the click, the original "most changed cells" tiebreak
        is unchanged -- this only narrows the candidate pool. When
        ``click_row_col`` is None (no click info available, e.g. a
        non-click action), behavior is unchanged from pre-A229: every
        entity with a bbox is eligible."""
        candidates: list[tuple[tuple[int, int, int, int], Mapping[str, Any]]] = []
        for entity in entities:
            attributes = getattr(entity, "attributes", None)
            bbox = attributes.get("bbox") if isinstance(attributes, Mapping) else None
            if not bbox or len(bbox) != 4:
                continue
            candidates.append((bbox, attributes))

        if click_row_col is not None:
            click_row, click_col = click_row_col
            candidates = [
                (bbox, attributes)
                for bbox, attributes in candidates
                if bbox[0] <= click_row <= bbox[2] and bbox[1] <= click_col <= bbox[3]
            ]

        best_ref: Any = None
        best_count = 0
        for bbox, attributes in candidates:
            min_row, min_col, max_row, max_col = bbox
            count = sum(
                1
                for cell in changed_cells
                if min_row <= cell.get("row", -1) <= max_row and min_col <= cell.get("col", -1) <= max_col
            )
            if count > best_count:
                best_count = count
                best_ref = attributes.get("entity_ref")
        return best_ref

    @staticmethod
    def _click_row_col(execution: ExecutionResult) -> tuple[int, int] | None:
        """A229: the (row, col) grid coordinate a click-shaped action
        targeted, read from the candidate's own payload (plan_generator.py's
        ACTION6 click-target generation stamps x/y there -- x is column, y
        is row, the same convention _click_targets itself uses). None for
        non-click actions (no x/y in payload) or when there's no candidate
        at all."""
        candidate = execution.candidate
        if candidate is None:
            return None
        payload = candidate.payload
        if not isinstance(payload, Mapping):
            return None
        x = payload.get("x")
        y = payload.get("y")
        if x is None or y is None:
            return None
        try:
            return int(y), int(x)
        except (TypeError, ValueError):
            return None

    def _attribute_entity_for_execution(
        self,
        execution: ExecutionResult,
        changed_cells: Sequence[Mapping[str, Any]],
        entities: Sequence[Any],
    ) -> Any:
        """A229: bbox-overlap attribution gated by the click coordinate that
        caused this step (when known), falling back to A218's
        _targeted_entity_ref -- the click's own known target -- when the
        click coordinate is known but no entity's bbox contains it. This is
        the same "we know what was clicked" signal A218 already uses on
        record_transition's no-op path, extended here to the real-change
        path for the one case bbox-overlap alone can't resolve. When the
        click coordinate isn't known (non-click action), behavior is
        unchanged from pre-A229: plain bbox-overlap, no fallback attempted."""
        click_row_col = self._click_row_col(execution)
        entity_ref = self._attribute_entity(changed_cells, entities, click_row_col)
        if entity_ref is None and click_row_col is not None:
            entity_ref = self._targeted_entity_ref(execution)
        return entity_ref

    @staticmethod
    def _targeted_entity_ref(execution: ExecutionResult) -> Any:
        """A218: the entity a click-shaped candidate targeted, read straight
        from the candidate's own metadata (plan_generator.py's ACTION6
        click-target generation stamps `entity_ref` there) -- independent of
        whether the click produced any visible change. Used by
        record_transition's no-op path, where _attribute_entity's
        bbox-overlap mechanism has nothing to attribute (no changed_cells to
        overlap a bbox against)."""
        candidate = execution.candidate
        if candidate is None:
            return None
        metadata = candidate.metadata
        if not isinstance(metadata, Mapping):
            return None
        return metadata.get("entity_ref")

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

        # Track call attempts for hypothesis confirmation/contradiction and goal confidence updates
        if tool_key in ("confirm_hypothesis", "contradict_hypothesis"):
            self._hypothesis_confirm_contradict_count += 1
        elif tool_key == "update_goal_confidence":
            self._goal_confidence_write_count += 1

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
                self._capability_missing_count += 1
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
                    self._capability_missing_count += 1
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
        # A175: arc_perceive_state keys ActionEntity identity on flat
        # color_id/region_index fields -- previously never sent, so every
        # entity collapsed to entity_id="{task}_e0_0" (MERGE onto one
        # degenerate node). Send the real color plus perceive.py's stable
        # frame-to-frame correspondence id (region_index), and the other
        # flat fields the write path actually reads. Omit "role" rather
        # than invent a value the client doesn't have -- the server's own
        # ent.get("role", "unknown") default applies.
        centroid = entity.attributes.get("centroid")
        centroid_row, centroid_col = (centroid[0], centroid[1]) if centroid else (None, None)
        return {
            "kind": entity.kind,
            "value": entity.value,
            "color_id": entity.attributes.get("color"),
            "region_index": entity.attributes.get("entity_ref"),
            "centroid_row": centroid_row,
            "centroid_col": centroid_col,
            "pixel_count": entity.attributes.get("cell_count"),
            "attributes": dict(entity.attributes),
        }

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
    def _vet_entity_ref(vet: VetDecision) -> Any | None:
        """B359: the click-target entity this vetted candidate targets, if
        any -- mirrors _vet_action_id's candidate-then-alternative fallback."""
        for source in (vet.candidate, vet.alternative):
            if source is not None and isinstance(source.metadata, Mapping):
                entity_ref = source.metadata.get("entity_ref")
                if entity_ref is not None:
                    return entity_ref
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