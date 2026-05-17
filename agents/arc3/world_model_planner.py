"""ARC World Model Guided Planner (A077).

Chooses evidence-backed experiments from the world model and mechanic priors.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from agents.arc3.world_model import COORDINATE_REQUIRED_ACTIONS

logger = logging.getLogger(__name__)

class PlanMode(str, Enum):
    EXPLOIT    = "exploit"
    PROBE      = "probe"
    FALSIFY    = "falsify"
    RECOVER    = "recover"
    TERMINATE  = "terminate"
    CLICK_PROBE = "click_probe"  # A108: Coordinate-aware click probing

@dataclass
class PlanCandidate:
    action_id: str
    args: Dict[str, Any]
    mode: PlanMode
    hypothesis_id: Optional[str] = None
    predicted_observation: Optional[Dict[str, Any]] = None  # A089: structured prediction
    falsification_condition: Optional[str] = None
    expected_gain: float = 0.0
    evidence_path: str = ""
    mechanic_prior_id: Optional[str] = None
    mechanic_prior_confidence: float = 0.0
    mechanic_prior_source: str = "none"
    prior_compatibility_score: float = 0.0  # A090: score for graph-prior compatibility
    route_actions: List[str] = field(default_factory=list)
    route_confidence: float = 0.0
    
    # A108: Click probe fields
    click_candidate_id: Optional[str] = None
    click_candidate_role: Optional[str] = None
    action_identity: Optional[str] = None  # A106: e.g., "ACTION6@10,12"


@dataclass
class PlanSelection:
    selected: PlanCandidate
    candidates: List[PlanCandidate]
    rationale: str
    candidate_count: int = 0
    selected_has_prediction: bool = False
    selected_has_falsification: bool = False
    mechanic_priors_used: int = 0
    selected_prior_id: Optional[str] = None  # A090: track selected prior provenance
    selected_prior_compatibility: float = 0.0  # A090: selected prior score


class WorldModelPlanner:
    """Proposes and ranks actions based on world model graph state."""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    @staticmethod
    def _prior_effect_class(effect: Dict[str, Any]) -> str:
        if effect.get("effect_class"):
            return str(effect.get("effect_class"))
        if effect.get("effect"):
            return str(effect.get("effect"))
        if effect.get("kind"):
            return str(effect.get("kind"))
        if effect.get("predicts_terminal_progress"):
            return "terminal_progress"
        if effect.get("predicts_object_progress"):
            return "object_progress"
        if effect.get("predicts_delayed_reward"):
            return "delayed_reward"
        return "unknown"

    def _normalize_prior_effects(
        self,
        prior: Dict[str, Any],
        available_actions: List[str],
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """Map aggregate mechanic evidence onto currently legal actions.

        Aggregate memory may describe a structurally similar game with different
        action ids. In a single-action environment, the only legal action is the
        executable counterpart for any prior effect pattern.
        """
        raw_effects = prior.get("effects") or prior.get("effect_patterns") or prior.get("action_effects") or []
        if isinstance(raw_effects, dict):
            raw_effects = [raw_effects]
        normalized: List[Dict[str, Any]] = []
        single_action = available_actions[0] if len(available_actions) == 1 else None

        for raw in list(raw_effects)[:limit]:
            if not isinstance(raw, dict):
                continue
            source_action = raw.get("action") or raw.get("action_id")
            action_id = source_action
            if action_id not in available_actions and single_action:
                action_id = single_action
            if action_id not in available_actions:
                continue
            effect = dict(raw)
            effect["action"] = action_id
            effect["source_action"] = source_action
            effect["effect_class"] = self._prior_effect_class(raw)
            normalized.append(effect)

        if not normalized and single_action and prior.get("predicts_delayed_reward"):
            normalized.append({
                "action": single_action,
                "source_action": None,
                "effect_class": "delayed_reward",
                "predicts_delayed_reward": True,
                "confidence": prior.get("confidence", 0.5),
            })
        return normalized

    def _generate_prediction_for_action(
        self,
        action_id: str,
        world_model: Any,
        prior: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """A089: Generate predicted observation for an action from graph evidence.
        
        Returns a structured prediction dict with effect_class, meaningful_progress,
        confidence, and evidence_path_ids, or None if no evidence.
        """
        # Check mechanic prior for explicit prediction
        if prior and prior.get("effects"):
            effects = prior.get("effects", [])
            for eff in effects:
                if eff.get("action") == action_id:
                    effect_class = self._prior_effect_class(eff)
                    if effect_class == "terminal_progress":
                        return {
                            "effect_class": "terminal_progress",
                            "meaningful_progress": True,
                            "confidence": eff.get("confidence", 0.7),
                            "evidence_path": [prior.get("id", "")]
                        }
                    if effect_class == "object_progress":
                        return {
                            "effect_class": "object_progress",
                            "meaningful_progress": True,
                            "confidence": eff.get("confidence", 0.7),
                            "evidence_path": [prior.get("id", "")]
                        }
                    if effect_class == "delayed_reward":
                        return {
                            "effect_class": "delayed_reward",
                            "meaningful_progress": True,
                            "confidence": eff.get("confidence", prior.get("confidence", 0.6)),
                            "evidence_path": [prior.get("id", "")]
                        }
        
        # A089: Check world model graph for prediction evidence
        try:
            pred_evidence = world_model.get_action_prediction_evidence(action_id)
            if pred_evidence and pred_evidence.get("effect_histogram"):
                # Select dominant effect class
                effect_hist = pred_evidence["effect_histogram"]
                effect_class = max(effect_hist.items(), key=lambda x: x[1])[0]
                
                # Determine meaningful progress
                meaningful = pred_evidence["meaningful_progress_rate"] > 0.3
                contradiction_penalty = min(0.4, float(pred_evidence.get("contradiction_count", 0) or 0) * 0.1)
                confidence = max(0.0, float(pred_evidence["confidence"] or 0.0) - contradiction_penalty)
                
                if effect_class != "unknown" and confidence > 0.4:
                    return {
                        "effect_class": effect_class,
                        "meaningful_progress": meaningful,
                        "confidence": confidence,
                        "evidence_path": pred_evidence["evidence_path_ids"]
                    }
        except Exception:
            pass
        
        return None

    def _generate_falsification_condition_for_action(
        self,
        action_id: str,
        candidate: PlanCandidate,
        world_model: Any,
        prior: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """A086: Generate falsification condition for an action from graph evidence.
        
        Handles both structured (dict) and string predictions from A089.
        """
        # For probe actions, falsification is: "no change after N probes"
        if candidate.mode == PlanMode.PROBE:
            return "Falsified if action produces no effect after 2-3 attempts"

        if candidate.mechanic_prior_id:
            return "Falsified if aggregate mechanic prior produces no matching effect after bounded probes"
        
        # For hypothesis-driven actions, check prediction
        if candidate.predicted_observation:
            # A089: Handle structured prediction dict
            if isinstance(candidate.predicted_observation, dict):
                effect_class = candidate.predicted_observation.get("effect_class", "")
                if "terminal" in effect_class.lower():
                    return "Falsified if terminal distance does not improve"
                elif "object" in effect_class.lower():
                    return "Falsified if no new objects appear or existing objects do not move"
                elif "delayed" in effect_class.lower():
                    return "Falsified if delayed effect remains absent after bounded tick probes"
                elif "coordinate" in effect_class.lower():
                    return "Falsified if coordinate pattern contradicts prediction"
            # Legacy: handle string predictions
            elif isinstance(candidate.predicted_observation, str):
                if "terminal" in candidate.predicted_observation.lower():
                    return "Falsified if terminal distance does not improve"
                elif "object" in candidate.predicted_observation.lower():
                    return "Falsified if no new objects appear or existing objects do not move"
                elif "coordinate" in candidate.predicted_observation.lower():
                    return "Falsified if coordinate pattern contradicts prediction"
        
        # Default falsification
        return None

    def _compute_prior_compatibility_score(
        self,
        prior: Dict[str, Any],
        world_model: Any,
        action_id: str
    ) -> float:
        """A090: Score how well a mechanic prior matches current graph state.
        
        Returns compatibility score (0.0-1.0) based on:
        - Action exists and has been tried
        - Effect histogram matches prior effect pattern
        - Contradictions reduce score
        - Graph-backed confidence boost
        """
        score = 0.0
        
        # Check if action has prior evidence
        try:
            pred_evidence = world_model.get_action_prediction_evidence(action_id)
        except Exception:
            pred_evidence = {"effect_histogram": {}, "contradiction_count": 0, "meaningful_progress_rate": 0.0}
        if not pred_evidence.get("effect_histogram"):
            # No evidence yet; use prior confidence as-is
            return prior.get("confidence", 0.5) * 0.6
        
        # Base score from prior confidence
        score = prior.get("confidence", 0.5)
        
        # Boost if prior effect pattern matches graph histogram
        prior_effects = prior.get("effects", [])
        for eff in prior_effects:
            if eff.get("action") == action_id:
                prior_effect_class = self._prior_effect_class(eff)
                if prior_effect_class in pred_evidence["effect_histogram"]:
                    score = min(1.0, score * 1.2)  # 20% boost for match
                elif pred_evidence["effect_histogram"].get("pixel_churn") and prior_effect_class in ("object_progress", "terminal_progress", "delayed_reward"):
                    score = max(0.0, score * 0.7)
        
        # Reduce score for contradictions
        contradiction_penalty = min(0.3, pred_evidence["contradiction_count"] * 0.1)
        score = max(0.0, score - contradiction_penalty)
        
        # Apply graph confidence (higher meaningful-progress rate increases score)
        if pred_evidence["meaningful_progress_rate"] > 0.4:
            score = min(1.0, score * 1.15)
        
        return score

    def _rank_candidates_by_evidence_backing(
        self,
        candidates: List[PlanCandidate],
        quarantined_actions: Optional[Set[str]] = None,
    ) -> List[PlanCandidate]:
        """A086: Rank candidates prioritizing prediction + falsification evidence.
        A090: Also consider prior compatibility score.
        """
        quarantined_actions = set(quarantined_actions or set())
        def score_candidate(c: PlanCandidate) -> tuple[int, float, float]:
            # Rank by: (evidence_tier, prior_boost, gain_score)
            # Tier -1: graph-backed progress exploit
            # Tier 0: predicted + falsifiable
            # Tier 1: falsifiable only
            # Tier 2: generic probe
            evidence_tier = 2
            if c.action_id in quarantined_actions:
                evidence_tier = 3
            if c.mode == PlanMode.EXPLOIT and c.evidence_path.startswith("productive_path:"):
                evidence_tier = -1
            elif c.mode == PlanMode.EXPLOIT and c.evidence_path.startswith("route_path:"):
                evidence_tier = -1
            elif c.mode == PlanMode.CLICK_PROBE and c.action_identity:
                evidence_tier = 0
            elif c.predicted_observation and c.falsification_condition:
                evidence_tier = 0
            elif c.falsification_condition:
                evidence_tier = 1
            if c.action_id in quarantined_actions:
                evidence_tier = 3
                c.expected_gain = min(c.expected_gain, 0.01)
            
            # A090: Boost compatible priors in ranking
            prior_boost = c.prior_compatibility_score if c.mechanic_prior_id else 0.0
            
            return (evidence_tier, -prior_boost, -c.expected_gain)  # Negative values for descending sort
        
        return sorted(candidates, key=score_candidate)

    def select_next_candidate(
        self,
        world_model: Any,
        mechanic_priors: List[Dict[str, Any]],
        available_actions: List[str],
        budget_state: Dict[str, Any]
    ) -> PlanSelection:
        candidates = []
        quarantined_actions = {
            str(a) for a in (budget_state.get("quarantined_actions") or []) if a
        }

        # 0. Generate exploit candidates directly from graph-backed causal paths.
        try:
            route_candidates = world_model.find_route_candidates(available_actions=available_actions)
        except Exception:
            route_candidates = []
        for route in route_candidates[:3]:
            aid = route.get("action_id")
            if aid not in available_actions or aid in quarantined_actions:
                continue
            if aid in COORDINATE_REQUIRED_ACTIONS:
                # Route evidence is action-level. For click-like actions, using it
                # without a candidate coordinate fabricates ACTION6@0,0 loops.
                continue
            confidence = float(route.get("route_confidence", 0.0) or 0.0)
            expected_delta = float(route.get("expected_distance_delta", 0.0) or 0.0)
            candidates.append(PlanCandidate(
                action_id=aid,
                args={"x": 0, "y": 0},
                mode=PlanMode.EXPLOIT,
                predicted_observation={
                    "effect_class": "distance_improving_move" if expected_delta < -0.01 else "state_transition",
                    "meaningful_progress": False,
                    "confidence": confidence,
                    "evidence_path": route.get("evidence_path", []),
                    "expected_distance_delta": expected_delta,
                },
                falsification_condition="Falsified if route action does not improve distance or reach a novel state",
                expected_gain=max(0.2, confidence + max(0.0, -expected_delta / 10.0)),
                evidence_path=f"route_path:{aid}:{','.join((route.get('evidence_path') or [])[:3])}",
                route_actions=list(route.get("route_actions") or [aid]),
                route_confidence=confidence,
            ))

        try:
            productive_paths = world_model.get_productive_action_paths(available_actions=available_actions)
        except Exception:
            productive_paths = []
        for path in productive_paths[:3]:
            aid = path.get("action_id")
            if aid not in available_actions:
                continue
            if aid in quarantined_actions:
                continue
            if aid in COORDINATE_REQUIRED_ACTIONS:
                continue
            evidence_ids = path.get("evidence_path_ids") or []
            confidence = float(path.get("confidence", 0.0) or 0.0)
            candidate = PlanCandidate(
                action_id=aid,
                args={"x": 0, "y": 0},
                mode=PlanMode.EXPLOIT,
                predicted_observation={
                    "effect_class": "object_progress",
                    "meaningful_progress": True,
                    "confidence": confidence,
                    "evidence_path": evidence_ids,
                    "support_count": int(path.get("support_count", 0) or 0),
                    "churn_count": int(path.get("churn_count", 0) or 0),
                    "recent_success_rate": float(path.get("recent_success_rate", 0.0) or 0.0),
                    "consecutive_misses_after_progress": int(path.get("consecutive_misses_after_progress", 0) or 0),
                },
                falsification_condition="Falsified if repeated action stops producing object or terminal progress",
                expected_gain=max(0.5, confidence + (0.1 * int(path.get("support_count", 0) or 0))),
                evidence_path=f"productive_path:{aid}:{','.join(evidence_ids[:3])}",
            )
            candidates.append(candidate)
        
        # 1. Generate candidates from active hypotheses
        active_hyps = world_model.get_active_hypotheses()
        for h in active_hyps:
             # Logic to map hypothesis to action...
             # For now, simple placeholder
             pass

        # 1b. Coordinate-aware click probes from the graph. These must outrank
        # generic ACTION6 probes so click-only games do not collapse to null
        # clicks with no target.
        tried_action_identities = {
            str(a) for a in (budget_state.get("quarantined_action_identities") or []) if a
        }
        try:
            active_goal_hypotheses = world_model.get_active_goal_hypotheses(limit=3)
        except Exception:
            active_goal_hypotheses = []
        click_candidates = self.rank_pattern_correspondence_candidates(
            world_model=world_model,
            available_actions=available_actions,
            active_goal_hypotheses=active_goal_hypotheses,
        )
        if not click_candidates:
            click_candidates = self.generate_click_probe_candidates(
                world_model=world_model,
                available_actions=available_actions,
                active_goal_hypotheses=active_goal_hypotheses,
            )
        for candidate in click_candidates[:8]:
            if candidate.action_id in quarantined_actions:
                continue
            if candidate.action_identity and candidate.action_identity in tried_action_identities:
                continue
            if candidate.predicted_observation is None:
                candidate.predicted_observation = {
                    "effect_class": "configuration_change",
                    "meaningful_progress": True,
                    "confidence": max(0.4, float(candidate.expected_gain or 0.0)),
                    "evidence_path": [candidate.click_candidate_id] if candidate.click_candidate_id else [],
                }
            if candidate.falsification_condition is None:
                candidate.falsification_condition = "Falsified if click produces no frame or configuration delta"
            candidates.append(candidate)
             
        # 2. Generate candidates from mechanic priors (with A089 graph-backed predictions)
        for prior in mechanic_priors:
            # A081: Attach prior provenance to candidates.
            p_effects = self._normalize_prior_effects(prior, available_actions)
            for eff in p_effects[:2]:
                aid = eff.get("action")
                if aid not in available_actions:
                    continue
                if aid in quarantined_actions:
                    continue
                if aid in COORDINATE_REQUIRED_ACTIONS:
                    continue
                normalized_prior = dict(prior)
                normalized_prior["effects"] = p_effects
                candidate = PlanCandidate(
                    action_id=aid,
                    args={},
                    mode=PlanMode.EXPLOIT,
                    mechanic_prior_id=prior.get("id"),
                    mechanic_prior_confidence=prior.get("confidence", 0.0),
                    mechanic_prior_source="aggregate",
                    expected_gain=prior.get("confidence", 0.5) * 0.8,
                    evidence_path=f"mechanic_prior_{prior.get('id')}"
                )
                # A089: Generate prediction and falsification from graph.
                candidate.predicted_observation = self._generate_prediction_for_action(aid, world_model, normalized_prior)
                candidate.falsification_condition = self._generate_falsification_condition_for_action(aid, candidate, world_model, normalized_prior)
                # A090: Score prior compatibility against graph state.
                candidate.prior_compatibility_score = self._compute_prior_compatibility_score(normalized_prior, world_model, aid)
                candidates.append(candidate)

             
        # 3. Default probe candidates for available actions (with A089 graph-backed predictions)
        for aid in available_actions[:3]:
             if aid in quarantined_actions:
                 continue
             if aid in COORDINATE_REQUIRED_ACTIONS:
                 continue
             candidate = PlanCandidate(
                 action_id=aid,
                 args={"x": 0, "y": 0},
                 mode=PlanMode.PROBE,
                 expected_gain=0.1,
                 evidence_path=f"untested_action_{aid}"
             )
             # A089: Generate prediction from graph evidence
             candidate.predicted_observation = self._generate_prediction_for_action(aid, world_model)
             # A086: Generate falsification for probes
             candidate.falsification_condition = self._generate_falsification_condition_for_action(aid, candidate, world_model)
             candidates.append(candidate)
             
        # 4. Handle single action stall recovery
        if len(available_actions) == 1:
             # If stalling, maybe terminate or try different args?
             pass
             
        # A086+A089+A090: Rank candidates by evidence backing and prior compatibility
        candidates = self._rank_candidates_by_evidence_backing(candidates, quarantined_actions=quarantined_actions)
        
        fallback_actions = [a for a in available_actions if a not in quarantined_actions] or list(available_actions)
        selected = candidates[0] if candidates else PlanCandidate(
            action_id=fallback_actions[0] if fallback_actions else "ACTION1",
            args={},
            mode=PlanMode.PROBE,
            evidence_path="fallback"
        )
        
        # A090: Prior is only "used" if selected candidate was influenced by it
        selected_uses_prior = 1 if (getattr(selected, "mechanic_prior_id", None) and selected.prior_compatibility_score > 0) else 0

        return PlanSelection(
            selected=selected,
            candidates=candidates,
            rationale=f"Selected {selected.action_id} via {selected.mode.value} mode.",
            candidate_count=len(candidates),
            selected_has_prediction=selected.predicted_observation is not None,
            selected_has_falsification=selected.falsification_condition is not None,
            mechanic_priors_used=selected_uses_prior,
            selected_prior_id=getattr(selected, "mechanic_prior_id", None),
            selected_prior_compatibility=float(getattr(selected, "prior_compatibility_score", 0.0) or 0.0),
        )

    # ── A103: Graph Transformation Predictions ──────────────────────────

    def get_transformation_class_predictions(
        self,
        world_model: Any,
        action_id: str,
        limit: int = 3,
    ) -> Dict[str, Any]:
        """A103: Get predictions based on recent graph transformations for an action.
        
        Args:
            world_model: WorldModelGraph for querying transformations.
            action_id: The action to predict for.
            limit: Max transformations to consider.
        
        Returns:
            Dict with transformation_class, confidence, goal_relevance.
        """
        try:
            transformations = world_model.get_recent_graph_transformations(action_id=action_id, limit=limit)
            if not transformations:
                return {"has_transformation_evidence": False}
            
            # Aggregate transformation classes
            classes = [t.get("transform_class") for t in transformations if t.get("transform_class")]
            if not classes:
                return {"has_transformation_evidence": False}
            
            # Use most common class
            dominant_class = max(set(classes), key=classes.count)
            avg_confidence = sum(t.get("confidence", 0.0) for t in transformations) / max(1, len(transformations))
            avg_relevance = sum(t.get("goal_relevance", 0.0) for t in transformations) / max(1, len(transformations))
            
            return {
                "has_transformation_evidence": True,
                "dominant_transformation_class": dominant_class,
                "confidence": avg_confidence,
                "goal_relevance": avg_relevance,
                "transformation_count": len(transformations),
            }
        except Exception:
            return {"has_transformation_evidence": False}

    # ── A104: Configuration Cycle Support ───────────────────────────────

    def suggest_configuration_cycle_next_action(
        self,
        available_actions: List[str],
        world_model: Any,
        cycle_search_state: Optional[Dict[str, Any]] = None,
    ) -> PlanCandidate:
        """A104: Suggest next action during configuration cycle search.
        
        In single-action games, this typically just cycles the single action.
        
        Args:
            available_actions: Legal actions for the current state.
            world_model: WorldModelGraph for querying cycle evidence.
            cycle_search_state: Optional state tracking seen configs.
        
        Returns:
            PlanCandidate for the cycle action.
        """
        if not available_actions:
            return PlanCandidate(action_id="ACTION1", args={}, mode=PlanMode.PROBE)
        
        # In single-action, just cycle the action
        action_id = available_actions[0]
        
        # Get transformation evidence to understand what this action does
        trans_evidence = world_model.get_configuration_cycle_evidence(action_id=action_id, limit=8)
        
        confidence = 0.7 if trans_evidence.get("transformation_count", 0) > 0 else 0.3
        
        return PlanCandidate(
            action_id=action_id,
            args={},
            mode=PlanMode.PROBE,
            expected_gain=0.1,
            evidence_path="configuration_cycle_probe",
        )

    # ── A108: Coordinate-aware click probe planning ──────────────────

    def generate_click_probe_candidates(
        self,
        world_model: Any,
        available_actions: List[str],
        active_goal_hypotheses: Optional[List[Dict[str, Any]]] = None,
    ) -> List[PlanCandidate]:
        """A108: Generate coordinate-aware click probe candidates.
        
        Returns candidates based on click-candidates in the world model,
        suitable for deterministic cheap probe iteration over coordinates.
        """
        candidates = []
        
        # Only generate click candidates for click-only or click-relevant games
        if "ACTION6" not in available_actions:
            return candidates
        
        # Get candidates from graph
        click_candidates = world_model.get_click_candidates(limit=16)
        
        if not click_candidates:
            return candidates
        
        # Convert each click candidate to a plan candidate
        for click_cand in click_candidates:
            x = click_cand.get("x")
            y = click_cand.get("y")
            if x is None or y is None:
                continue
            try:
                x = int(x)
                y = int(y)
            except (TypeError, ValueError):
                continue
            cand = PlanCandidate(
                action_id="ACTION6",
                args={
                    "x": x,
                    "y": y,
                },
                mode=PlanMode.CLICK_PROBE,
                click_candidate_id=click_cand.get("id"),
                click_candidate_role=click_cand.get("role"),
                expected_gain=click_cand.get("confidence", 0.5) * 0.8,
                evidence_path=f"click_candidate:{click_cand.get('id')}",
            )
            
            # A106: Compute action identity with coordinates
            from agents.arc3.world_model import build_action_identity
            cand.action_identity = build_action_identity("ACTION6", x, y)
            
            candidates.append(cand)
        
        return candidates

    def suggest_click_probe_action(
        self,
        world_model: Any,
        available_actions: List[str],
        quarantined_actions: Set[str] = None,
        quarantined_action_identities: Set[str] = None,
        step_num: int = 0,
    ) -> Optional[PlanCandidate]:
        """A108: Suggest the next click probe action for cheap probe mode.
        
        Selects the next untried click candidate, skipping quarantined coordinates.
        Returns None if all candidates are exhausted or no candidates available.
        """
        quarantined_actions = quarantined_actions or set()
        quarantined_action_identities = quarantined_action_identities or set()
        
        # Generate candidates
        candidates = self.generate_click_probe_candidates(world_model, available_actions)
        
        if not candidates:
            return None
        
        # Filter out quarantined candidates
        available_candidates = [
            c for c in candidates
            if c.action_identity not in quarantined_action_identities
            and c.action_id not in quarantined_actions
        ]
        
        if not available_candidates:
            return None
        
        # Sort by confidence (already sorted by generation, but ensure ranking)
        available_candidates.sort(
            key=lambda c: (-c.expected_gain, c.click_candidate_id or "")
        )
        
        return available_candidates[0]

    # ── A109: Pattern Correspondence Goal Planner ────────────────────

    def rank_pattern_correspondence_candidates(
        self,
        world_model: Any,
        available_actions: List[str],
        active_goal_hypotheses: Optional[List[Dict[str, Any]]] = None,
    ) -> List[PlanCandidate]:
        """A109: Rank click candidates for pattern/color-correspondence goals.
        
        Returns candidates ranked by:
        1. Candidates in target/framed panel (high confidence)
        2. Mismatch cells that differ from source panels
        3. Centers of framed/gray/white motifs
        4. Penalizes already falsified
        """
        if "ACTION6" not in available_actions:
            return []
        
        candidates = []
        
        # Find pattern correspondence candidates
        corr_candidates = world_model.find_pattern_correspondence_candidates(
            goal_type="color_correspondence",
            limit=16
        )
        
        for corr_cand in corr_candidates:
            x = corr_cand.get("x")
            y = corr_cand.get("y")
            if x is None or y is None:
                continue
            try:
                x = int(x)
                y = int(y)
            except (TypeError, ValueError):
                continue
            # Generate plan candidate
            plan_cand = PlanCandidate(
                action_id="ACTION6",
                args={
                    "x": x,
                    "y": y,
                },
                mode=PlanMode.CLICK_PROBE,
                click_candidate_id=corr_cand.get("id"),
                click_candidate_role=corr_cand.get("role"),
                expected_gain=corr_cand.get("confidence", 0.5) * 0.9,
                evidence_path=f"pattern_corr:{corr_cand.get('id')}",
            )
            
            # A106: Set action identity
            from agents.arc3.world_model import build_action_identity
            plan_cand.action_identity = build_action_identity("ACTION6", x, y)
            
            # A109: Generate prediction for pattern correspondence
            plan_cand.predicted_observation = {
                "effect_class": "configuration_change",
                "goal_type": "color_correspondence",
                "meaningful_progress": True,
                "confidence": 0.7,
                "evidence_path": [corr_cand.get("id")],
                "role": corr_cand.get("role"),
            }
            
            # A109: Falsification condition based on candidate role
            if corr_cand.get("role") == "framed_center":
                plan_cand.falsification_condition = "No frame/center change after click"
            elif corr_cand.get("role") == "mismatch_cell":
                plan_cand.falsification_condition = "Mismatch not resolved after click"
            else:
                plan_cand.falsification_condition = "Click produces no configuration delta"
            
            candidates.append(plan_cand)
        
        # Sort by expected gain (confidence)
        candidates.sort(key=lambda c: -c.expected_gain)
        
        return candidates

    def suggest_pattern_correspondence_action(
        self,
        world_model: Any,
        available_actions: List[str],
        active_goal_hypotheses: Optional[List[Dict[str, Any]]] = None,
        quarantined_action_identities: Set[str] = None,
    ) -> Optional[PlanCandidate]:
        """A109: Suggest next action for pattern correspondence goal.
        
        Ranks candidates and returns the best untried action.
        """
        quarantined_action_identities = quarantined_action_identities or set()
        
        # Get ranked candidates
        candidates = self.rank_pattern_correspondence_candidates(
            world_model=world_model,
            available_actions=available_actions,
            active_goal_hypotheses=active_goal_hypotheses,
        )
        
        if not candidates:
            return None
        
        # Filter out quarantined
        available = [
            c for c in candidates
            if c.action_identity not in quarantined_action_identities
        ]
        
        return available[0] if available else None
