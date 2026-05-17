"""ARC Per-Game World Model Graph (A073).

Provides a bounded, auditable causal structure for ARC observations and beliefs.
Uses a lightweight in-memory labeled property graph (LPG).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# A106: Coordinate-aware action identity
COORDINATE_REQUIRED_ACTIONS = {"ACTION6"}  # Click-like actions that require coordinates


def build_action_identity(action_id: str, x: int | None = None, y: int | None = None) -> str:
    """A106: Build coordinate-aware action identity for coordinate-required actions.
    
    For coordinate-required actions like ACTION6, returns "ACTION6@x,y".
    For other actions, returns action_id as-is.
    """
    if action_id in COORDINATE_REQUIRED_ACTIONS and x is not None and y is not None:
        return f"{action_id}@{int(x)},{int(y)}"
    return action_id


@dataclass
class WorldNode:
    """A single vertex in the world model graph."""
    id: str
    label: str
    props: Dict[str, Any] = field(default_factory=dict)

@dataclass
class WorldEdge:
    """A directed edge between nodes in the world model graph."""
    src: str
    rel: str
    dst: str
    props: Dict[str, Any] = field(default_factory=dict)

class WorldModelGraph:
    """Bounded, in-memory LPG for ARC causal modeling."""

    def __init__(self, task_id: str, session_id: str):
        self.task_id = task_id
        self.session_id = session_id
        self.nodes: Dict[str, WorldNode] = {}
        self.edges: List[WorldEdge] = []
        
        # fast lookup indices
        self._out_edges: Dict[str, List[int]] = {}
        self._in_edges: Dict[str, List[int]] = {}

        # A080: Counters
        self.contradiction_count = 0
        self.demotion_count = 0
        
        # A093: Action quarantine state (by action_id)
        self._action_quarantine: Dict[str, Dict[str, Any]] = {}  # action_id -> {quarantined_until_step, reason, falsification_count}
        self._action_falsification_count: Dict[str, int] = {}  # action_id -> count
        
        # A106: Action identity (coordinate-aware) quarantine state
        self._action_identity_quarantine: Dict[str, Dict[str, Any]] = {}  # action_identity -> {quarantined_until_step, reason, falsification_count}
        self._action_identity_falsification_count: Dict[str, int] = {}  # action_identity -> count

        # CAPS
        self.MAX_NODES_PER_LABEL = 200
        self.MAX_ACTIVE_HYPOTHESES = 20
        self.MAX_DEMOTED_HYPOTHESES = 50
        
        # Initialize Game root
        self.game_node_id = f"game-{task_id}-{session_id[:8]}"
        self.add_node(self.game_node_id, "Game", {
            "task_id": task_id,
            "session_id": session_id
        })

    def add_node(self, node_id: str, label: str, props: Dict[str, Any]) -> str:
        # Check label caps
        label_count = sum(1 for n in self.nodes.values() if n.label == label)
        if label_count >= self.MAX_NODES_PER_LABEL:
            # Simple LRU-style pruning or just skip if it's too high?
            # For now, we skip to keep the graph bounded and deterministic.
            if node_id not in self.nodes:
                return node_id

        if node_id not in self.nodes:
            self.nodes[node_id] = WorldNode(node_id, label, props)
        else:
            self.nodes[node_id].props.update(props)
        return node_id

    def add_edge(self, src: str, rel: str, dst: str, props: Dict[str, Any] = None) -> None:
        if src not in self.nodes or dst not in self.nodes:
            return
            
        edge_idx = len(self.edges)
        edge = WorldEdge(src, rel, dst, props or {})
        self.edges.append(edge)
        
        self._out_edges.setdefault(src, []).append(edge_idx)
        self._in_edges.setdefault(dst, []).append(edge_idx)

    # ── Mutation Helpers ──────────────────────────────────────────────

    def record_state(self, step: int, frame_hash: str) -> str:
        node_id = f"state-{self.task_id}-{step}-{frame_hash[:8]}"
        self.add_node(node_id, "State", {"step": step, "hash": frame_hash})
        self.add_edge(self.game_node_id, "HAS_STATE", node_id)
        return node_id

    def record_action(self, step: int, action_id: str, args: Dict[str, Any], state_id: str) -> str:
        # Stable ID for action based on step and signature
        args_sig = hashlib.md5(json.dumps(args, sort_keys=True).encode()).hexdigest()[:8]
        node_id = f"action-{self.task_id}-{step}-{action_id}-{args_sig}"
        
        # A106: Compute coordinate-aware action identity
        x = args.get("x")
        y = args.get("y")
        action_identity = build_action_identity(action_id, x, y)
        coordinate_required = action_id in COORDINATE_REQUIRED_ACTIONS
        missing_coordinate_click = coordinate_required and (x is None or y is None)
        
        self.add_node(node_id, "Action", {
            "action_id": action_id,
            "action_identity": action_identity,
            "args": args,
            "args_signature": args_sig,
            "step": step,
            "coordinate_required": coordinate_required,
            "missing_coordinate_click": missing_coordinate_click,
        })
        self.add_edge(state_id, "ACTION_TAKEN", node_id, {"step": step})
        return node_id

    def record_observation(self, step: int, frame_hash: str, reward: float, terminal_score: float) -> str:
        node_id = f"obs-{self.task_id}-{step}-{frame_hash[:8]}"
        self.add_node(node_id, "Observation", {
            "step": step,
            "frame_hash": frame_hash,
            "reward": reward,
            "terminal_score": terminal_score
        })
        return node_id

    def record_effect(self, action_node_id: str, obs_node_id: str, kind: str, props: Dict[str, Any]) -> str:
        # Effect is derived from action -> observation
        node_id = f"effect-{action_node_id}-{obs_node_id[:16]}-{kind}"
        self.add_node(node_id, "Effect", {
            "kind": kind,
            **props
        })
        self.add_edge(action_node_id, "CAUSED", node_id, {"step": props.get("step")})
        self.add_edge(node_id, "OBSERVED_IN", obs_node_id)
        return node_id

    @staticmethod
    def _is_route_transition_effect(kind: str, props: Dict[str, Any]) -> bool:
        if kind in {"distance_improving_move", "reversible_movement", "state_transition"}:
            return True
        try:
            return float(props.get("goal_distance_delta", 0.0) or 0.0) < -0.01
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _is_terminal_productive_effect(kind: str, props: Dict[str, Any]) -> bool:
        """Return true only for progress evidence aligned with the terminal objective."""
        if kind == "terminal_progress":
            return True
        if kind not in {"object_progress", "meaningful_progress"}:
            return False
        if "terminal_alignment" not in props and "terminal_aligned" not in props:
            return bool(props.get("meaningful", True))
        alignment = str(props.get("terminal_alignment") or "")
        if alignment in {"terminal_aligned", "delayed_effect_pending"}:
            return True
        return bool(props.get("terminal_aligned", False))

    def upsert_hypothesis(self, h_id: str, scope: str, claim: str, confidence: float, status: str) -> str:
        node_id = f"hyp-{h_id}"
        
        # A080: Track demotion transition
        if node_id in self.nodes:
             prev_status = self.nodes[node_id].props.get("status")
             if prev_status == "active" and status == "demoted":
                  self.demotion_count += 1
                  
        self.add_node(node_id, "Hypothesis", {
            "id": h_id,
            "scope": scope,
            "claim": claim,
            "confidence": confidence,
            "status": status
        })
        return node_id

    def link_support(self, obs_node_id: str, hyp_node_id: str, weight: float, reason: str) -> None:
        self.add_edge(obs_node_id, "SUPPORTS", hyp_node_id, {"weight": weight, "reason": reason})

    def link_contradiction(self, obs_node_id: str, hyp_node_id: str, weight: float, reason: str) -> None:
        self.add_edge(obs_node_id, "CONTRADICTS", hyp_node_id, {"weight": weight, "reason": reason})
        self.contradiction_count += 1

    # ── Query Helpers ─────────────────────────────────────────────────

    def get_action_effect_table(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Returns a summary of recent action-effect causal pairs."""
        results = []
        # Find recent Action nodes
        actions = sorted(
            [n for n in self.nodes.values() if n.label == "Action"],
            key=lambda n: n.props.get("step", 0),
            reverse=True
        )[:limit]
        
        for act in actions:
            # Find CAUSED effects
            effect_indices = self._out_edges.get(act.id, [])
            for idx in effect_indices:
                edge = self.edges[idx]
                if edge.rel == "CAUSED":
                    eff = self.nodes.get(edge.dst)
                    if eff:
                        results.append({
                            "step": act.props.get("step"),
                            "action": act.props.get("action_id"),
                            "effect": eff.props.get("kind"),
                            "magnitude": eff.props.get("magnitude", 0),
                            "meaningful": eff.props.get("meaningful", False)
                        })
        return results

    def get_recent_action_effects(self, action_id: str, limit: int = 5) -> Dict[str, Any]:
        """A086: Get recent effects for a specific action from graph evidence."""
        effects = {"action_id": action_id, "recent_effects": [], "primary_effect": "unknown", "effect_count": 0}
        
        # Find recent instances of this action
        action_nodes = [
            n for n in self.nodes.values() 
            if n.label == "Action" and n.props.get("action_id") == action_id
        ]
        
        # Sort by step, get most recent
        action_nodes.sort(key=lambda n: n.props.get("step", -1), reverse=True)
        
        for act in action_nodes[:limit]:
            # Find CAUSED effects
            effect_indices = self._out_edges.get(act.id, [])
            for idx in effect_indices:
                edge = self.edges[idx]
                if edge.rel == "CAUSED":
                    eff = self.nodes.get(edge.dst)
                    if eff:
                        effect_kind = eff.props.get("kind", "unknown")
                        if effect_kind in {"object_progress", "meaningful_progress"} and not self._is_terminal_productive_effect(effect_kind, eff.props):
                            effect_kind = "local_object_progress"
                        effects["recent_effects"].append(effect_kind)
                        if not effects["primary_effect"] or effects["primary_effect"] == "unknown":
                            effects["primary_effect"] = effect_kind
        
        effects["effect_count"] = len(effects["recent_effects"])
        return effects

    def get_action_prediction_evidence(self, action_id: str, limit: int = 5) -> Dict[str, Any]:
        """A089: Get bounded prediction evidence for an action from graph history.
        
        Returns effect histogram, meaningful-progress rate, contradiction count,
        and bounded evidence path IDs for use in planner predictions.
        """
        result = {
            "action_id": action_id,
            "effect_histogram": {},
            "meaningful_progress_rate": 0.0,
            "contradiction_count": 0,
            "evidence_path_ids": [],
            "confidence": 0.0
        }
        
        # Find recent instances of this action
        action_nodes = [
            n for n in self.nodes.values() 
            if n.label == "Action" and n.props.get("action_id") == action_id
        ]
        
        if not action_nodes:
            return result
        
        # Sort by step, get most recent
        action_nodes.sort(key=lambda n: n.props.get("step", -1), reverse=True)
        action_nodes = action_nodes[:limit]
        
        total_effects = 0
        meaningful_effects = 0
        evidence_ids = set()
        
        for act in action_nodes:
            evidence_ids.add(act.id)
            # Find CAUSED effects
            effect_indices = self._out_edges.get(act.id, [])
            for idx in effect_indices:
                edge = self.edges[idx]
                if edge.rel == "CAUSED":
                    eff = self.nodes.get(edge.dst)
                    if eff:
                        effect_kind = eff.props.get("kind", "unknown")
                        if effect_kind in {"object_progress", "meaningful_progress"} and not self._is_terminal_productive_effect(effect_kind, eff.props):
                            effect_kind = "local_object_progress"
                        result["effect_histogram"][effect_kind] = result["effect_histogram"].get(effect_kind, 0) + 1
                        total_effects += 1
                        if self._is_terminal_productive_effect(effect_kind, eff.props):
                            meaningful_effects += 1
                        evidence_ids.add(eff.id)
        
        # Compute meaningful progress rate
        if total_effects > 0:
            result["meaningful_progress_rate"] = meaningful_effects / total_effects
            result["confidence"] = min(0.95, 0.5 + result["meaningful_progress_rate"])
        
        # Check for contradictions against this action
        hyp_nodes = [n for n in self.nodes.values() if n.label == "Hypothesis"]
        for hyp in hyp_nodes:
            hyp_idx = [e for e in range(len(self.edges)) if self.edges[e].dst == hyp.id]
            for idx in hyp_idx:
                edge = self.edges[idx]
                if edge.rel == "CONTRADICTS":
                    # Check if contradiction is related to action effects
                    if hyp.props.get("status") == "demoted":
                        result["contradiction_count"] += 1
        
        # Bounded evidence path (cap to 5 IDs)
        result["evidence_path_ids"] = sorted(list(evidence_ids))[:5]
        
        return result

    def get_productive_action_paths(
        self,
        available_actions: Optional[List[str]] = None,
        limit: int = 5,
        lookback: int = 12,
    ) -> List[Dict[str, Any]]:
        """Return graph-backed action paths that causally support progress.

        This is the operational LPG query used by the planner/policy layer:
        Action -[:CAUSED]-> Effect(kind in progress classes) -[:OBSERVED_IN]-> Observation.
        Results are bounded and include enough path ids to explain the decision.
        """
        allowed = set(available_actions or [])
        harmful_effects = {"harmful"}
        churn_effects = {"pixel_churn", "visual_churn", "no_op", "none"}
        action_nodes = [
            n for n in self.nodes.values()
            if n.label == "Action" and (not allowed or n.props.get("action_id") in allowed)
        ]
        if not action_nodes:
            return []

        action_nodes.sort(key=lambda n: n.props.get("step", -1), reverse=True)
        recent_action_nodes = action_nodes[: max(1, lookback)]
        summaries: Dict[str, Dict[str, Any]] = {}

        for act in recent_action_nodes:
            action_id = act.props.get("action_id")
            if not action_id:
                continue
            summary = summaries.setdefault(action_id, {
                "action_id": action_id,
                "support_count": 0,
                "churn_count": 0,
                "harmful_count": 0,
                "contradiction_count": 0,
                "last_step": -1,
                "last_progress_step": -1,
                "consecutive_misses_after_progress": 0,
                "recent_success_rate": 0.0,
                "effect_histogram": {},
                "recent_effects": [],
                "evidence_path_ids": [],
                "confidence": 0.0,
            })
            summary["last_step"] = max(summary["last_step"], int(act.props.get("step", -1) or -1))
            if len(summary["evidence_path_ids"]) < limit:
                summary["evidence_path_ids"].append(act.id)

            for idx in self._out_edges.get(act.id, []):
                edge = self.edges[idx]
                if edge.rel != "CAUSED":
                    continue
                eff = self.nodes.get(edge.dst)
                if not eff:
                    continue
                raw_kind = eff.props.get("kind", "unknown")
                kind = raw_kind
                if raw_kind in {"object_progress", "meaningful_progress"} and not self._is_terminal_productive_effect(raw_kind, eff.props):
                    kind = "local_object_progress"
                summary["recent_effects"].append(kind)
                summary["effect_histogram"][kind] = summary["effect_histogram"].get(kind, 0) + 1
                if self._is_terminal_productive_effect(raw_kind, eff.props):
                    summary["support_count"] += 1
                    summary["last_progress_step"] = max(summary["last_progress_step"], int(act.props.get("step", -1) or -1))
                    if len(summary["evidence_path_ids"]) < limit:
                        summary["evidence_path_ids"].append(eff.id)
                    for obs_idx in self._out_edges.get(eff.id, []):
                        obs_edge = self.edges[obs_idx]
                        if obs_edge.rel == "OBSERVED_IN" and len(summary["evidence_path_ids"]) < limit:
                            summary["evidence_path_ids"].append(obs_edge.dst)
                elif kind in harmful_effects:
                    summary["harmful_count"] += 1
                elif kind in churn_effects or kind == "local_object_progress":
                    summary["churn_count"] += 1

        for summary in summaries.values():
            support = int(summary["support_count"])
            churn = int(summary["churn_count"])
            harmful = int(summary["harmful_count"])
            total = max(1, support + churn + harmful)
            recent_effects = list(summary.get("recent_effects") or [])
            misses_after_progress = 0
            for effect in recent_effects:
                if effect in {"object_progress", "terminal_progress", "meaningful_progress"}:
                    break
                misses_after_progress += 1
            summary["consecutive_misses_after_progress"] = misses_after_progress
            summary["recent_success_rate"] = support / total
            summary["contradiction_count"] = harmful + churn
            if support > 0:
                base = summary["recent_success_rate"]
                confidence = min(0.95, 0.25 + (0.7 * base))
                confidence -= min(0.45, 0.12 * misses_after_progress)
                confidence -= min(0.3, 0.1 * harmful)
                summary["confidence"] = max(0.0, confidence)

        productive = [
            summary for summary in summaries.values()
            if summary["support_count"] > 0 and summary["confidence"] >= 0.3
        ]
        productive.sort(
            key=lambda s: (
                -float(s["confidence"]),
                -float(s["recent_success_rate"]),
                int(s["consecutive_misses_after_progress"]),
                int(s["harmful_count"]),
                -int(s["support_count"]),
                -int(s["last_step"]),
                str(s["action_id"]),
            )
        )
        return productive[:limit]

    def get_all_actions_churn_evidence(
        self,
        available_actions: Optional[List[str]] = None,
        lookback: int = 18,
        min_tests_per_action: int = 2,
    ) -> Dict[str, Any]:
        """Return bounded evidence that every legal action is currently churn.

        This is intentionally an entry-point filtered traversal over recent
        Action nodes and their CAUSED Effect nodes, avoiding full-graph scans in
        the hot loop.
        """
        actions = [str(a) for a in (available_actions or []) if a]
        if not actions:
            actions = sorted({
                str(n.props.get("action_id"))
                for n in self.nodes.values()
                if n.label == "Action" and n.props.get("action_id")
            })
        if not actions:
            return {
                "all_actions_churn": False,
                "actions_tested_count": 0,
                "required_action_count": 0,
                "total_churn_count": 0,
                "total_progress_count": 0,
                "action_summaries": {},
                "evidence_path_ids": [],
            }

        allowed = set(actions)
        action_nodes = [
            n for n in self.nodes.values()
            if n.label == "Action" and n.props.get("action_id") in allowed
        ]
        action_nodes.sort(key=lambda n: n.props.get("step", -1), reverse=True)

        summaries = {
            aid: {"tested_count": 0, "churn_count": 0, "progress_count": 0, "harmful_count": 0, "local_progress_count": 0}
            for aid in actions
        }
        evidence_ids: List[str] = []
        churn_effects = {"pixel_churn", "visual_churn", "no_op", "none"}

        for act in action_nodes[: max(1, lookback)]:
            aid = str(act.props.get("action_id"))
            if aid not in summaries:
                continue
            summaries[aid]["tested_count"] += 1
            if len(evidence_ids) < 8:
                evidence_ids.append(act.id)
            for idx in self._out_edges.get(act.id, []):
                edge = self.edges[idx]
                if edge.rel != "CAUSED":
                    continue
                eff = self.nodes.get(edge.dst)
                if not eff:
                    continue
                kind = str(eff.props.get("kind", "unknown"))
                if len(evidence_ids) < 8:
                    evidence_ids.append(eff.id)
                if self._is_terminal_productive_effect(kind, eff.props):
                    summaries[aid]["progress_count"] += 1
                elif self._is_route_transition_effect(kind, eff.props):
                    summaries[aid]["local_progress_count"] += 1
                elif kind in churn_effects:
                    summaries[aid]["churn_count"] += 1
                elif kind in {"object_progress", "meaningful_progress"}:
                    summaries[aid]["local_progress_count"] += 1
                    summaries[aid]["churn_count"] += 1
                elif kind == "harmful":
                    summaries[aid]["harmful_count"] += 1

        actions_tested = sum(1 for s in summaries.values() if s["tested_count"] > 0)
        total_churn = sum(int(s["churn_count"]) for s in summaries.values())
        total_progress = sum(int(s["progress_count"]) for s in summaries.values())
        total_local_progress = sum(int(s.get("local_progress_count", 0)) for s in summaries.values())
        all_tested_enough = all(int(s["tested_count"]) >= min_tests_per_action for s in summaries.values())
        no_progress = total_progress == 0
        churn_on_all = all(int(s["churn_count"]) > 0 for s in summaries.values())

        return {
            "all_actions_churn": bool(all_tested_enough and no_progress and churn_on_all),
            "actions_tested_count": actions_tested,
            "required_action_count": len(actions),
            "total_churn_count": total_churn,
            "total_progress_count": total_progress,
            "total_local_progress_count": total_local_progress,
            "min_tests_per_action": min_tests_per_action,
            "action_summaries": summaries,
            "evidence_path_ids": evidence_ids[:8],
        }

    def get_route_transition_evidence(
        self,
        available_actions: Optional[List[str]] = None,
        lookback: int = 18,
        limit: int = 8,
    ) -> Dict[str, Any]:
        """Return bounded evidence for route-useful state transitions."""
        allowed = {str(a) for a in (available_actions or []) if a}
        action_nodes = [
            n for n in self.nodes.values()
            if n.label == "Action" and (not allowed or str(n.props.get("action_id")) in allowed)
        ]
        action_nodes.sort(key=lambda n: n.props.get("step", -1), reverse=True)

        transitions: List[Dict[str, Any]] = []
        best_delta: Optional[float] = None
        novel_states: Set[str] = set()
        evidence_ids: List[str] = []
        route_effects = {"distance_improving_move", "distance_regressing_move", "reversible_movement", "state_transition"}

        for act in action_nodes[: max(1, lookback)]:
            aid = str(act.props.get("action_id") or "")
            if not aid:
                continue
            if len(evidence_ids) < limit:
                evidence_ids.append(act.id)
            for idx in self._out_edges.get(act.id, []):
                edge = self.edges[idx]
                if edge.rel != "CAUSED":
                    continue
                eff = self.nodes.get(edge.dst)
                if not eff:
                    continue
                kind = str(eff.props.get("kind", "unknown"))
                is_route = kind in route_effects or self._is_route_transition_effect(kind, eff.props)
                if not is_route:
                    continue
                delta = eff.props.get("goal_distance_delta")
                try:
                    delta_float = float(delta) if delta is not None else None
                except (TypeError, ValueError):
                    delta_float = None
                if delta_float is not None:
                    best_delta = delta_float if best_delta is None else min(best_delta, delta_float)
                for obs_idx in self._out_edges.get(eff.id, []):
                    obs_edge = self.edges[obs_idx]
                    if obs_edge.rel == "OBSERVED_IN":
                        obs = self.nodes.get(obs_edge.dst)
                        if obs and obs.props.get("frame_hash"):
                            novel_states.add(str(obs.props.get("frame_hash")))
                        if len(evidence_ids) < limit:
                            evidence_ids.append(obs_edge.dst)
                if len(evidence_ids) < limit:
                    evidence_ids.append(eff.id)
                transitions.append({
                    "action_id": aid,
                    "effect_class": kind,
                    "step": int(act.props.get("step", 0) or 0),
                    "goal_distance_delta": delta_float,
                    "distance_trend": eff.props.get("distance_trend"),
                    "goal_distance_after": eff.props.get("goal_distance_after", eff.props.get("goal_distance")),
                    "evidence_path_ids": [act.id, eff.id],
                })
                if len(transitions) >= limit:
                    break
            if len(transitions) >= limit:
                break

        # Preserve a recency view before sorting. The action scan is newest-first,
        # and this is the signal the controller needs to avoid following a stale
        # historical route while the current route is moving away from the goal.
        recent_transitions = list(transitions[:limit])
        recent_regression_streak = 0
        for transition in recent_transitions:
            delta = transition.get("goal_distance_delta")
            is_regressing = (
                transition.get("effect_class") == "distance_regressing_move"
                or transition.get("distance_trend") == "regressing"
                or (delta is not None and float(delta) > 0.01)
            )
            if not is_regressing:
                break
            recent_regression_streak += 1

        transitions.sort(key=lambda t: (
            float(t["goal_distance_delta"]) if t.get("goal_distance_delta") is not None else 999.0,
            -int(t.get("step", 0) or 0),
            str(t.get("action_id") or ""),
        ))
        improving_count = sum(1 for t in transitions if (t.get("goal_distance_delta") is not None and float(t["goal_distance_delta"]) < -0.01) or t.get("effect_class") == "distance_improving_move")
        regressing_count = sum(1 for t in transitions if (t.get("goal_distance_delta") is not None and float(t["goal_distance_delta"]) > 0.01) or t.get("effect_class") == "distance_regressing_move")
        recent_regressing_count = sum(
            1
            for t in recent_transitions
            if (t.get("goal_distance_delta") is not None and float(t["goal_distance_delta"]) > 0.01)
            or t.get("effect_class") == "distance_regressing_move"
            or t.get("distance_trend") == "regressing"
        )
        net_distance_delta = sum(
            float(t["goal_distance_delta"])
            for t in transitions
            if t.get("goal_distance_delta") is not None
        )
        return {
            "has_route_evidence": bool(transitions),
            "transition_count": len(transitions),
            "improving_transition_count": improving_count,
            "regressing_transition_count": regressing_count,
            "recent_regressing_count": recent_regressing_count,
            "recent_regression_streak": recent_regression_streak,
            "net_distance_delta": net_distance_delta,
            "has_recent_route_regression": bool(
                recent_regression_streak >= 3
                or (recent_regressing_count >= 3 and regressing_count > improving_count)
            ),
            "best_distance_delta": best_delta,
            "novel_state_count": len(novel_states),
            "transitions": transitions[:limit],
            "evidence_path_ids": evidence_ids[:limit],
        }

    def find_route_candidates(
        self,
        available_actions: Optional[List[str]] = None,
        max_depth: int = 4,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Return deterministic first-action route candidates from transition evidence."""
        evidence = self.get_route_transition_evidence(
            available_actions=available_actions,
            lookback=max(8, max_depth * 4),
            limit=max(limit * 2, limit),
        )
        candidates: Dict[str, Dict[str, Any]] = {}
        for transition in evidence.get("transitions", []):
            aid = transition.get("action_id")
            if not aid:
                continue
            candidate = candidates.setdefault(str(aid), {
                "action_id": str(aid),
                "route_actions": [str(aid)],
                "expected_distance_delta": 0.0,
                "route_confidence": 0.25,
                "evidence_path": [],
                "transition_count": 0,
            })
            delta = transition.get("goal_distance_delta")
            if delta is not None:
                candidate["expected_distance_delta"] += float(delta)
            candidate["transition_count"] += 1
            candidate["route_confidence"] = min(0.95, 0.35 + (0.15 * candidate["transition_count"]))
            candidate["evidence_path"].extend(transition.get("evidence_path_ids") or [])
        for candidate in candidates.values():
            candidate["evidence_path"] = list(dict.fromkeys(candidate["evidence_path"]))[:6]
        ranked = sorted(
            candidates.values(),
            key=lambda c: (
                float(c.get("expected_distance_delta", 0.0) or 0.0),
                -float(c.get("route_confidence", 0.0) or 0.0),
                str(c.get("action_id") or ""),
            ),
        )
        non_regressing = [
            candidate
            for candidate in ranked
            if float(candidate.get("expected_distance_delta", 0.0) or 0.0) <= 0.0
        ]
        return non_regressing[:limit]

    def get_active_hypotheses(self, limit: int = 5) -> List[Dict[str, Any]]:
        return [
            n.props for n in self.nodes.values() 
            if n.label == "Hypothesis" and n.props.get("status") == "active"
        ][:limit]

    def to_prompt_summary(self, max_chars: int = 2000) -> str:
        """Generates a compact textual representation for LLM consumption."""
        lines = ["WORLD MODEL GRAPH SUMMARY:"]
        
        hyps = self.get_active_hypotheses()
        if hyps:
            lines.append("Active Hypotheses:")
            for h in hyps:
                lines.append(f"- [{h['scope']}] {h['claim']} (conf: {h['confidence']:.2f})")
                
        effects = self.get_action_effect_table(limit=5)
        if effects:
            lines.append("Recent Action Effects:")
            for e in effects:
                meaningful = "meaningful" if e['meaningful'] else "churn"
                lines.append(f"- Step {e['step']}: {e['action']} -> {e['effect']} ({meaningful})")
                
        summary = "\n".join(lines)
        if len(summary) > max_chars:
            return summary[:max_chars] + "... [truncated]"
        return summary

    def compact_world_model_delta(self, max_chars: int = 1500, last_node_count: int = 0) -> str:
        """A095: Generates compressed delta view for prompt reuse.
        
        Only shows changes since last cycle: new hypotheses, active contradictions,
        recent effects, and action set updates. Preserves evidence paths for graph reconstruction.
        """
        lines = ["WORLD MODEL DELTA:"]
        
        # Show only new hypotheses (estimate by confidence and recency)
        hyps = self.get_active_hypotheses()
        recent_hyps = sorted(hyps, key=lambda h: h.get('confidence', 0.0), reverse=True)[:2]
        if recent_hyps:
            lines.append("Recent Hypotheses (Δ):")
            for h in recent_hyps:
                lines.append(f"- [{h['scope']}] {h['claim']} (conf: {h['confidence']:.2f})")
        
        # Show contradiction pressure
        if self.contradiction_count > 0:
            lines.append(f"Contradictions: {self.contradiction_count} active")
        
        # Show recent effects (just the last 3)
        effects = self.get_action_effect_table(limit=3)
        if effects:
            lines.append("Recent Effects (last 3):")
            for e in effects:
                meaningful = "✓" if e['meaningful'] else "✗"
                lines.append(f"- {e['action']} → {e['effect']} {meaningful}")
        
        # Show quarantined actions
        quarantined = [aid for aid in self._action_quarantine.keys()]
        if quarantined:
            lines.append(f"Quarantined Actions: {', '.join(quarantined[:3])}")
        
        # Show graph growth indicator
        node_count = len(self.nodes)
        delta_nodes = node_count - last_node_count
        if delta_nodes > 0:
            lines.append(f"Graph growth: +{delta_nodes} nodes")
        
        summary = "\n".join(lines)
        if len(summary) > max_chars:
            return summary[:max_chars] + "... [truncated]"
        return summary

    def to_trace_snapshot(self) -> Dict[str, Any]:
        """Full serializable snapshot for debug/persistence."""
        return {
            "task_id": self.task_id,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "contradiction_count": self.contradiction_count,
            "demotion_count": self.demotion_count,
            "nodes": {nid: {"label": n.label, "props": n.props} for nid, n in self.nodes.items()},
            "edges": [{"src": e.src, "rel": e.rel, "dst": e.dst, "props": e.props} for e in self.edges]
        }

    def get_action_set_signature(self) -> str:
        """A075: Normalized signature of all actions tried in this game."""
        actions = {n.props.get("action_id") for n in self.nodes.values() if n.label == "Action"}
        return ",".join(sorted(list(actions)))

    def to_mechanic_summary(self) -> Dict[str, Any]:
        """A075: Aggregate per-game evidence into a reusable mechanic summary."""
        active_hyps = self.get_active_hypotheses()
        effects = self.get_action_effect_table(limit=20)
        churn_evidence = self.get_all_actions_churn_evidence()
        
        # Determine primary mechanic name from most confident hypothesis or task_id
        name = "unknown_mechanic"
        if active_hyps:
             name = active_hyps[0].get("claim", name)[:50]
             
        return {
            "id": f"mech-{self.task_id}-{hashlib.md5(name.encode()).hexdigest()[:8]}",
            "name": name,
            "task_id": self.task_id,
            "action_set_signature": self.get_action_set_signature(),
            "hypotheses": active_hyps,
            "effects": effects,
            "failure_modes": [
                "all_actions_churn_no_progress"
            ] if churn_evidence.get("all_actions_churn") else [],
            "all_actions_churn_evidence": churn_evidence,
            "confidence": max([h.get("confidence", 0.0) for h in active_hyps]) if active_hyps else 0.0,
            "timestamp_iso": getattr(self, "_creation_time", "") # Can be added if needed
        }


    def apply_compiled_delta(self, delta: Any) -> None:
        """A074: Apply structured claims from WorldModelCompiler."""
        # We use duck typing or casts for CompiledWorldDelta/CompiledClaim
        step = getattr(delta, "step", 0)
        claims = getattr(delta, "claims", [])
        
        for claim in claims:
            kind = getattr(claim, "kind", "unknown")
            props = getattr(claim, "props", {})
            
            if kind == "action_effect":
                action_id = getattr(claim, "action_id", "unknown")
                effect_class = getattr(claim, "effect_class", "unknown")
                
                # Find the action node for this step
                # (Assuming record_action was already called)
                args_sig = props.get("args_signature", "") # May need to pass this through
                # For now, search by step and action_id
                action_nodes = [n for n in self.nodes.values() if n.label == "Action" and n.props.get("step") == step and n.props.get("action_id") == action_id]
                if action_nodes:
                    act_node = action_nodes[0]
                    # Record the effect
                    obs_nodes = [n for n in self.nodes.values() if n.label == "Observation" and n.props.get("step") == step]
                    if obs_nodes:
                        obs_node = obs_nodes[0]
                        self.record_effect(act_node.id, obs_node.id, effect_class, {
                            "step": step,
                            "meaningful": props.get("meaningful", False),
                            "magnitude": props.get("magnitude", 0),
                            "terminal_aligned": props.get("terminal_aligned", False),
                            "terminal_alignment": props.get("terminal_alignment"),
                            "terminal_alignment_reason": props.get("terminal_alignment_reason"),
                            "goal_distance": props.get("goal_distance"),
                            "goal_distance_before": props.get("goal_distance_before"),
                            "goal_distance_after": props.get("goal_distance_after"),
                            "goal_distance_delta": props.get("goal_distance_delta"),
                            "distance_trend": props.get("distance_trend"),
                        })

    # ── A093: Prediction Falsification & Quarantine ──────────────────

    def record_prediction_falsification(self, action_id: str, predicted_effect: str, actual_effect: str, step: int, confidence: float) -> None:
        """A093: Record that an action's prediction was falsified.
        
        Tracks count and triggers quarantine if we see 2 high-confidence (>=0.7) misses.
        """
        self._action_falsification_count[action_id] = self._action_falsification_count.get(action_id, 0) + 1
        count = self._action_falsification_count[action_id]
        
        # Only quarantine if this miss is high-confidence AND count >= 2
        if count >= 2 and confidence >= 0.7:
            # Check if we actually have 2+ high-confidence misses
            # For now, assume that if confidence is high on this one and count >= 2, quarantine
            self.quarantine_action(action_id, step + 5, f"predicted_{predicted_effect}_but_{actual_effect}")

    def quarantine_action(self, action_id: str, quarantine_until_step: int, reason: str) -> None:
        """A093: Temporarily quarantine an action from exploit selection."""
        self._action_quarantine[action_id] = {
            "quarantined_until_step": quarantine_until_step,
            "reason": reason,
            "falsification_count": self._action_falsification_count.get(action_id, 0)
        }

    def is_action_quarantined(self, action_id: str, current_step: int) -> bool:
        """A093: Check if an action is currently quarantined."""
        if action_id not in self._action_quarantine:
            return False
        
        quarantine_state = self._action_quarantine[action_id]
        if current_step >= quarantine_state["quarantined_until_step"]:
            # TTL expired, remove quarantine
            del self._action_quarantine[action_id]
            return False
        
        return True

    def get_quarantine_state(self, action_id: str) -> Optional[Dict[str, Any]]:
        """A093: Get quarantine metadata for an action."""
        return self._action_quarantine.get(action_id)

    # ── A106: Coordinate-aware action identity quarantine ───────────────────

    def record_action_identity_falsification(self, action_identity: str, predicted_effect: str, actual_effect: str, step: int, confidence: float) -> None:
        """A106: Record that an action identity's prediction was falsified.
        
        Tracks count and triggers quarantine if we see 2 high-confidence (>=0.7) misses.
        For coordinate-aware actions like ACTION6@x,y, quarantine only the specific coordinate pair.
        """
        self._action_identity_falsification_count[action_identity] = self._action_identity_falsification_count.get(action_identity, 0) + 1
        count = self._action_identity_falsification_count[action_identity]
        
        # Only quarantine if this miss is high-confidence AND count >= 2
        if count >= 2 and confidence >= 0.7:
            self.quarantine_action_identity(action_identity, step + 5, f"predicted_{predicted_effect}_but_{actual_effect}")

    def quarantine_action_identity(self, action_identity: str, quarantine_until_step: int, reason: str) -> None:
        """A106: Temporarily quarantine an action identity (e.g., ACTION6@10,12) from cheap probe selection."""
        self._action_identity_quarantine[action_identity] = {
            "quarantined_until_step": quarantine_until_step,
            "reason": reason,
            "falsification_count": self._action_identity_falsification_count.get(action_identity, 0)
        }

    def is_action_identity_quarantined(self, action_identity: str, current_step: int) -> bool:
        """A106: Check if an action identity is currently quarantined."""
        if action_identity not in self._action_identity_quarantine:
            return False
        
        quarantine_state = self._action_identity_quarantine[action_identity]
        if current_step >= quarantine_state["quarantined_until_step"]:
            # TTL expired, remove quarantine
            del self._action_identity_quarantine[action_identity]
            return False
        
        return True

    def get_action_identity_quarantine_state(self, action_identity: str) -> Optional[Dict[str, Any]]:
        """A106: Get quarantine metadata for an action identity."""
        return self._action_identity_quarantine.get(action_identity)

    # ── A101: Goal Hypotheses ──────────────────────────────────────────

    def upsert_goal_hypothesis(self, hypothesis: Any) -> str:
        """A101: Store or update a goal hypothesis in the graph.
        
        Args:
            hypothesis: GoalHypothesis dataclass with id, goal_type, claim, confidence, etc.
        
        Returns:
            The node id of the hypothesis.
        """
        hyp_dict = hypothesis.to_dict() if hasattr(hypothesis, "to_dict") else hypothesis
        node_id = f"goal-hyp-{hyp_dict['id']}"
        
        self.add_node(node_id, "GoalHypothesis", {
            "id": hyp_dict["id"],
            "goal_type": hyp_dict.get("goal_type", "unknown"),
            "claim": hyp_dict.get("claim", ""),
            "confidence": hyp_dict.get("confidence", 0.0),
            "status": hyp_dict.get("status", "active"),
            "target_object_ids": hyp_dict.get("target_object_ids", []),
            "evidence_path_ids": hyp_dict.get("evidence_path_ids", []),
        })
        
        self.add_edge(self.game_node_id, "HAS_GOAL_HYPOTHESIS", node_id)
        return node_id

    def get_active_goal_hypotheses(self, limit: int = 3) -> List[Dict[str, Any]]:
        """A101: Get active goal hypotheses, sorted by confidence."""
        hyps = [
            n.props for n in self.nodes.values()
            if n.label == "GoalHypothesis" and n.props.get("status") == "active"
        ]
        hyps.sort(key=lambda h: h.get("confidence", 0.0), reverse=True)
        return hyps[:limit]

    # ── A102: Mechanic Graph ──────────────────────────────────────────

    def apply_mechanic_graph_snapshot(self, snapshot: Any) -> None:
        """A102: Store a mechanic graph snapshot in the world model.
        
        Args:
            snapshot: MechanicGraphSnapshot with objects and relations.
        """
        # Create a frame node for this snapshot
        frame_node_id = f"mech-frame-{snapshot.frame_hash[:8]}-step{snapshot.step}"
        self.add_node(frame_node_id, "MechanicGraphFrame", {
            "step": snapshot.step,
            "frame_hash": snapshot.frame_hash,
            "configuration_hash": snapshot.configuration_hash,
            "object_count": len(snapshot.objects),
            "relation_count": len(snapshot.relations),
        })
        
        self.add_edge(self.game_node_id, "HAS_MECHANIC_FRAME", frame_node_id)
        
        # Store object nodes
        for obj_id, obj in snapshot.objects.items():
            obj_node_id = f"mech-obj-{obj.signature}-{obj_id}"
            self.add_node(obj_node_id, "MechanicObject", {
                "object_id": obj_id,
                "signature": obj.signature,
                "color": obj.color,
                "shape_kind": obj.shape_kind,
                "centroid": obj.centroid,
                "area": obj.area,
                "confidence": obj.confidence,
            })
            self.add_edge(frame_node_id, "HAS_OBJECT", obj_node_id)
        
        # Store relation nodes
        for rel in snapshot.relations:
            rel_node_id = f"mech-rel-{rel.src}-{rel.rel}-{rel.dst}"
            self.add_node(rel_node_id, "MechanicRelation", {
                "src_id": rel.src,
                "rel_type": rel.rel,
                "dst_id": rel.dst,
                "confidence": rel.confidence,
            })
            self.add_edge(frame_node_id, "HAS_RELATION", rel_node_id)

    def get_current_mechanic_graph_summary(self, max_objects: int = 12, max_edges: int = 24) -> Dict[str, Any]:
        """A102: Get a compact summary of the current mechanic graph."""
        # Find most recent mechanic frame
        frame_nodes = sorted(
            [n for n in self.nodes.values() if n.label == "MechanicGraphFrame"],
            key=lambda n: n.props.get("step", 0),
            reverse=True
        )
        
        if not frame_nodes:
            return {
                "configuration_hash": "",
                "object_count": 0,
                "relation_count": 0,
                "objects": [],
                "relations": [],
            }
        
        frame_node = frame_nodes[0]
        
        # Get objects and relations from this frame
        obj_nodes = []
        rel_nodes = []
        
        for edge_idx in self._out_edges.get(frame_node.id, []):
            edge = self.edges[edge_idx]
            if edge.rel == "HAS_OBJECT":
                obj_node = self.nodes.get(edge.dst)
                if obj_node:
                    obj_nodes.append(obj_node)
            elif edge.rel == "HAS_RELATION":
                rel_node = self.nodes.get(edge.dst)
                if rel_node:
                    rel_nodes.append(rel_node)
        
        objects_summary = [
            {
                "id": n.props.get("object_id"),
                "shape": n.props.get("shape_kind"),
                "color": n.props.get("color"),
                "area": n.props.get("area"),
            }
            for n in obj_nodes[:max_objects]
        ]
        
        relations_summary = [
            {
                "from": n.props.get("src_id"),
                "type": n.props.get("rel_type"),
                "to": n.props.get("dst_id"),
                "confidence": n.props.get("confidence"),
            }
            for n in rel_nodes[:max_edges]
        ]
        
        return {
            "configuration_hash": frame_node.props.get("configuration_hash", ""),
            "object_count": frame_node.props.get("object_count", 0),
            "relation_count": frame_node.props.get("relation_count", 0),
            "objects": objects_summary,
            "relations": relations_summary,
        }

    # ── A103: Graph Transformations ──────────────────────────────────

    def record_graph_transformation(self, transformation: Any) -> str:
        """A103: Store a graph transformation record.
        
        Args:
            transformation: GraphTransformation dataclass.
        
        Returns:
            Node id of the transformation.
        """
        trans_dict = transformation.to_dict() if hasattr(transformation, "to_dict") else transformation
        
        trans_node_id = f"transform-{trans_dict['action_id']}-{trans_dict['step']}"
        self.add_node(trans_node_id, "GraphTransformation", {
            "action_id": trans_dict.get("action_id"),
            "step": trans_dict.get("step"),
            "transform_class": trans_dict.get("transform_class"),
            "confidence": trans_dict.get("confidence", 0.0),
            "before_config_hash": trans_dict.get("before_config_hash"),
            "after_config_hash": trans_dict.get("after_config_hash"),
            "affected_object_ids": trans_dict.get("affected_object_ids", []),
            "goal_relevance": trans_dict.get("goal_relevance", 0.0),
        })
        
        self.add_edge(self.game_node_id, "HAS_TRANSFORMATION", trans_node_id)
        return trans_node_id

    def get_recent_graph_transformations(self, action_id: Optional[str] = None, limit: int = 8) -> List[Dict[str, Any]]:
        """A103: Get recent graph transformations, optionally filtered by action."""
        trans_nodes = [
            n for n in self.nodes.values()
            if n.label == "GraphTransformation"
            and (action_id is None or n.props.get("action_id") == action_id)
        ]
        
        trans_nodes.sort(key=lambda n: n.props.get("step", 0), reverse=True)
        
        return [
            {
                "action_id": n.props.get("action_id"),
                "step": n.props.get("step"),
                "transform_class": n.props.get("transform_class"),
                "confidence": n.props.get("confidence"),
                "goal_relevance": n.props.get("goal_relevance"),
            }
            for n in trans_nodes[:limit]
        ]

    # ── A104: Configuration Cycle Search ───────────────────────────────

    def get_configuration_cycle_evidence(self, action_id: str, limit: int = 32) -> Dict[str, Any]:
        """A104: Get evidence about configuration cycles for an action."""
        transformations = self.get_recent_graph_transformations(action_id=action_id, limit=limit)
        
        # Check if we see repeated configuration hashes
        config_hashes = []
        for trans in transformations:
            config_hashes.append(trans.get("after_config_hash", ""))
        
        unique_hashes = list(set(config_hashes))
        repeat_count = len(config_hashes) - len(unique_hashes)
        
        return {
            "action_id": action_id,
            "transformation_count": len(transformations),
            "unique_configuration_count": len(unique_hashes),
            "configuration_repeat_count": repeat_count,
            "avg_goal_relevance": sum(t.get("goal_relevance", 0.0) for t in transformations) / max(1, len(transformations)),
            "transformation_classes": list(set(t.get("transform_class") for t in transformations if t.get("transform_class"))),
        }

    # ── A105: Level Solution Templates ──────────────────────────────────

    def record_level_solution_template(self, template: Any) -> str:
        """A105: Store a level solution template for transfer.
        
        Args:
            template: LevelSolutionTemplate dataclass.
        
        Returns:
            Node id of the template.
        """
        tmpl_dict = template.to_dict() if hasattr(template, "to_dict") else template
        
        tmpl_node_id = f"lvl-template-{tmpl_dict['id']}"
        self.add_node(tmpl_node_id, "LevelSolutionTemplate", {
            "id": tmpl_dict["id"],
            "game_id": tmpl_dict.get("game_id"),
            "level_index": tmpl_dict.get("level_index"),
            "goal_type": tmpl_dict.get("goal_type"),
            "goal_relation_signature": tmpl_dict.get("goal_relation_signature"),
            "mechanic_signature": tmpl_dict.get("mechanic_signature"),
            "action_transform_signature": tmpl_dict.get("action_transform_signature"),
            "confidence": tmpl_dict.get("confidence", 0.0),
        })
        
        self.add_edge(self.game_node_id, "HAS_LEVEL_TEMPLATE", tmpl_node_id)
        return tmpl_node_id

    def get_level_solution_templates(self, limit: int = 8) -> List[Dict[str, Any]]:
        """A105: Get stored level solution templates."""
        tmpl_nodes = [
            n for n in self.nodes.values()
            if n.label == "LevelSolutionTemplate"
        ]
        
        tmpl_nodes.sort(key=lambda n: (n.props.get("confidence", 0.0), n.props.get("level_index", 0)), reverse=True)
        
        return [
            {
                "id": n.props.get("id"),
                "game_id": n.props.get("game_id"),
                "level_index": n.props.get("level_index"),
                "goal_type": n.props.get("goal_type"),
                "mechanic_signature": n.props.get("mechanic_signature"),
                "confidence": n.props.get("confidence"),
            }
            for n in tmpl_nodes[:limit]
        ]

    # ── A107: Click Candidates ───────────────────────────────────────────

    def upsert_click_candidates(self, candidates: List[Dict[str, Any]], frame_hash: str) -> None:
        """A107: Store clickable candidates for a frame in the graph.
        
        Args:
            candidates: List of candidate dictionaries with id, x, y, role, etc.
            frame_hash: Frame hash for grouping candidates.
        """
        candidates_node_id = f"click-candidates-{frame_hash[:8]}"
        self.add_node(candidates_node_id, "ClickCandidates", {
            "frame_hash": frame_hash,
            "candidate_count": len(candidates),
        })
        
        self.add_edge(self.game_node_id, "HAS_CLICK_CANDIDATES", candidates_node_id)
        
        # Store individual candidate nodes
        for cand in candidates:
            cand_node_id = f"click-{cand.get('id', 'unknown')}"
            self.add_node(cand_node_id, "ClickableCandidate", {
                "id": cand.get("id"),
                "x": cand.get("x"),
                "y": cand.get("y"),
                "color": cand.get("color"),
                "role": cand.get("role"),
                "confidence": cand.get("confidence"),
                "rank": cand.get("rank"),
                "source_object_id": cand.get("source_object_id"),
                "panel_id": cand.get("panel_id"),
                "goal_type": cand.get("goal_type"),
            })
            
            self.add_edge(candidates_node_id, "HAS_CANDIDATE", cand_node_id)
            
            # Link to source objects if available
            source_obj_id = cand.get("source_object_id")
            if source_obj_id:
                # Try to find the source object node
                source_nodes = [n for n in self.nodes.values() if n.props.get("object_id") == source_obj_id]
                if source_nodes:
                    self.add_edge(cand_node_id, "POINTS_TO", source_nodes[0].id)

    def get_click_candidates(self, goal_type: Optional[str] = None, limit: int = 16) -> List[Dict[str, Any]]:
        """A107: Get recent click candidates, optionally filtered by goal type."""
        # Find all ClickCandidates nodes
        candidate_group_nodes = [n for n in self.nodes.values() if n.label == "ClickCandidates"]
        
        # Sort by frame hash to get most recent
        candidate_group_nodes.sort(key=lambda n: n.props.get("frame_hash", ""), reverse=True)
        
        candidates = []
        
        # Collect candidates from most recent groups
        for group_node in candidate_group_nodes[:3]:  # Look in 3 most recent frames
            # Find all candidates in this group
            cand_indices = self._out_edges.get(group_node.id, [])
            for idx in cand_indices:
                edge = self.edges[idx]
                if edge.rel == "HAS_CANDIDATE":
                    cand_node = self.nodes.get(edge.dst)
                    if cand_node and cand_node.label == "ClickableCandidate":
                        # Filter by goal type if requested
                        if goal_type and cand_node.props.get("goal_type") != goal_type:
                            continue
                        
                        candidates.append({
                            "id": cand_node.props.get("id"),
                            "x": cand_node.props.get("x"),
                            "y": cand_node.props.get("y"),
                            "color": cand_node.props.get("color"),
                            "role": cand_node.props.get("role"),
                            "confidence": cand_node.props.get("confidence"),
                            "rank": cand_node.props.get("rank"),
                            "goal_type": cand_node.props.get("goal_type"),
                        })
        
        # Sort by confidence and rank
        candidates.sort(key=lambda c: (-c.get("confidence", 0.0), c.get("rank", 0)))
        
        return candidates[:limit]

    def get_click_candidate_by_id(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        """A107: Get a click candidate by id."""
        # Search for candidate node
        for node in self.nodes.values():
            if node.label == "ClickableCandidate" and node.props.get("id") == candidate_id:
                return {
                    "id": node.props.get("id"),
                    "x": node.props.get("x"),
                    "y": node.props.get("y"),
                    "color": node.props.get("color"),
                    "role": node.props.get("role"),
                    "confidence": node.props.get("confidence"),
                    "rank": node.props.get("rank"),
                    "goal_type": node.props.get("goal_type"),
                }
        
        return None

    # ── A109: Pattern Correspondence Goal Planner ────────────────────

    def find_pattern_correspondence_candidates(
        self,
        goal_type: str = "color_correspondence",
        limit: int = 16
    ) -> List[Dict[str, Any]]:
        """A109: Find candidates ranked for pattern correspondence goals.
        
        Compares panel signatures and ranks candidates that test color/pattern relations.
        """
        # Get active goal hypotheses
        active_goals = self.get_active_goal_hypotheses(limit=1)
        
        # Get click candidates matching this goal type
        candidates = self.get_click_candidates(goal_type=goal_type, limit=limit)
        
        # Rank by role (prefer framed, then mismatches, then centers)
        role_priority = {
            "framed_center": 0.9,
            "mismatch_cell": 0.8,
            "goal_target_center": 0.8,
            "matches_color_endpoint": 0.7,
            "panel_center": 0.5,
            "object_center": 0.4,
            "unknown": 0.1,
        }
        
        for cand in candidates:
            role = cand.get("role", "unknown")
            cand["role_priority"] = role_priority.get(role, 0.1)
        
        # Sort by role priority and confidence
        candidates.sort(
            key=lambda c: (-c.get("role_priority", 0.1), -c.get("confidence", 0.0))
        )
        
        return candidates[:limit]

    def find_panel_mismatches(self, limit: int = 8) -> List[Dict[str, Any]]:
        """A109: Find cells/objects that differ from pattern correspondence."""
        # This is a simplified implementation that returns mismatch candidates
        mismatches = self.get_click_candidates(goal_type="pattern_completion", limit=limit)
        
        return [
            {
                "id": m.get("id"),
                "x": m.get("x"),
                "y": m.get("y"),
                "role": "mismatch_cell",
                "expected_color": m.get("color"),
                "confidence": 0.6,
            }
            for m in mismatches
            if m.get("role") in {"mismatch_cell", "goal_target_center"}
        ][:limit]

    def get_click_candidate_evidence(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        """A109: Get full evidence path for a click candidate decision."""
        cand = self.get_click_candidate_by_id(candidate_id)
        
        if not cand:
            return None
        
        # Trace back to supporting nodes
        evidence = {
            "candidate_id": candidate_id,
            "candidate": cand,
            "evidence_path_ids": [],
            "supporting_goals": [],
            "supporting_objects": [],
        }
        
        # Find nodes that support this candidate
        for node in self.nodes.values():
            if node.label == "GoalHypothesis":
                evidence["supporting_goals"].append({
                    "id": node.props.get("id"),
                    "goal_type": node.props.get("goal_type"),
                    "confidence": node.props.get("confidence"),
                })
            elif node.label == "MechanicObject":
                evidence["supporting_objects"].append({
                    "id": node.props.get("object_id"),
                    "color": node.props.get("color"),
                    "confidence": node.props.get("confidence"),
                })
        
        return evidence
