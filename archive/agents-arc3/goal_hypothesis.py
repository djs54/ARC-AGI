"""A101 — Goal Hypothesis Induction From Game Objects.

Deterministic goal-hypothesis layer that turns object correspondences into explicit,
graph-backed hypotheses before the planner evaluates actions.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class GoalType(str, Enum):
    """Enumeration of detectable goal types from object patterns."""
    COLOR_CORRESPONDENCE = "color_correspondence"
    REACH_TARGET = "reach_target"
    COLLECT_OR_ACTIVATE = "collect_or_activate"
    LEVEL_ADVANCE = "level_advance"
    ENDPOINT_CONNECTION = "endpoint_connection"
    UNKNOWN = "unknown"


@dataclass
class GoalHypothesis:
    """An explicit goal hypothesis derived from object correspondences and game state."""
    id: str
    goal_type: GoalType
    claim: str
    confidence: float  # 0.0 to 1.0
    status: str  # "active", "demoted", "falsified"
    evidence_path_ids: List[str] = field(default_factory=list)
    target_object_ids: List[int] = field(default_factory=list)  # Object signatures or scene node ids
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for graph storage."""
        return {
            "id": self.id,
            "goal_type": self.goal_type.value,
            "claim": self.claim,
            "confidence": self.confidence,
            "status": self.status,
            "evidence_path_ids": self.evidence_path_ids,
            "target_object_ids": self.target_object_ids,
            "properties": self.properties,
        }


class GoalHypothesisInducer:
    """Deterministic goal hypothesis induction from objects and terminal context."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.color_map: Dict[int, str] = {}  # color value -> name for easier matching

    def induce(
        self,
        objects: List[Dict[str, Any]],
        world_model: Any,  # WorldModelGraph
        terminal_context: Dict[str, Any],
        env_signals: Optional[Dict[str, Any]] = None,
    ) -> List[GoalHypothesis]:
        """Induce bounded goal hypotheses from object correspondences and context.
        
        Args:
            objects: List of scene objects with color, shape, position properties.
            world_model: The WorldModelGraph for querying evidence.
            terminal_context: Current terminal score, levels_completed, etc.
            env_signals: Optional environment signals for level/progress tracking.
        
        Returns:
            List of goal hypotheses, sorted by confidence.
        """
        hypotheses: List[GoalHypothesis] = []

        available_actions = {
            str(action)
            for action in (terminal_context.get("available_actions") or [])
            if action
        }
        keyboard_navigation_surface = bool(
            "ACTION6" not in available_actions
            and len(available_actions.intersection({"ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"})) >= 4
        )

        # Detector 1: Color correspondence (matching colored objects)
        color_hyp = self._detect_color_correspondence(objects, world_model)
        if color_hyp:
            if keyboard_navigation_surface:
                color_hyp.confidence = min(color_hyp.confidence, 0.35)
                color_hyp.status = "demoted"
                color_hyp.properties["demotion_reason"] = "keyboard_navigation_controls"
            if color_hyp.confidence >= 0.45:
                hypotheses.append(color_hyp)

        # Detector 2: Reach/connect target (player-like to goal-like object)
        reach_hyp = self._detect_reach_target(objects, world_model)
        if reach_hyp:
            hypotheses.append(reach_hyp)

        # Detector 3: Collect or activate (object to endpoint/marker)
        collect_hyp = self._detect_collect_or_activate(objects, world_model)
        if collect_hyp:
            hypotheses.append(collect_hyp)

        # Detector 4: Level advance (supported by levels_completed changes)
        level_hyp = self._detect_level_advance(terminal_context, env_signals, world_model)
        if level_hyp:
            hypotheses.append(level_hyp)

        # Detector 5: Endpoint connection (rings/hubs connected to endpoints)
        endpoint_hyp = self._detect_endpoint_connection(objects, world_model)
        if endpoint_hyp:
            hypotheses.append(endpoint_hyp)

        # Sort by confidence descending
        hypotheses.sort(key=lambda h: h.confidence, reverse=True)

        return hypotheses[:10]  # Cap at 10 hypotheses

    def _detect_color_correspondence(
        self,
        objects: List[Dict[str, Any]],
        world_model: Any,
    ) -> Optional[GoalHypothesis]:
        """Detect if game goal is to match colors or create color patterns."""
        if not objects or len(objects) < 2:
            return None

        # Group objects by color
        color_groups: Dict[int, List[int]] = {}
        for i, obj in enumerate(objects):
            color = obj.get("color", 0)
            if color not in color_groups:
                color_groups[color] = []
            color_groups[color].append(i)

        # If we have multiple same-colored objects that are spatially distinct,
        # this suggests color matching or correspondence goal
        colored_groups = [
            group for color, group in color_groups.items()
            if color > 0 and len(group) >= 2
        ]
        if not colored_groups:
            return None

        # Estimate confidence based on group distinctness and color variety
        group_count = len(colored_groups)
        confidence = min(0.85, 0.5 + (0.1 * group_count))

        target_object_ids = []
        for group in colored_groups[:5]:
            target_object_ids.extend(group)

        hyp_id = f"goal-color-match-{hashlib.md5(str(target_object_ids).encode()).hexdigest()[:8]}"

        return GoalHypothesis(
            id=hyp_id,
            goal_type=GoalType.COLOR_CORRESPONDENCE,
            claim=f"Match or arrange {group_count} colored object groups",
            confidence=confidence,
            status="active",
            target_object_ids=target_object_ids,
            evidence_path_ids=[],
            properties={
                "color_group_count": group_count,
                "detector": "color_correspondence",
            },
        )

    def _detect_reach_target(
        self,
        objects: List[Dict[str, Any]],
        world_model: Any,
    ) -> Optional[GoalHypothesis]:
        """Detect if goal is to move a player-like object to a goal-like object."""
        if not objects or len(objects) < 2:
            return None

        # Look for object with distinct color/shape (player) and another object (target)
        player_candidates = []
        goal_candidates = []

        for i, obj in enumerate(objects):
            shape = obj.get("shape_kind", "unknown")
            color = obj.get("color", 0)
            area = obj.get("area", 0)

            # Player: typically distinct, moveable-sized
            if area > 0 and area < 500 and color not in {0}:
                player_candidates.append((i, obj))

            # Target/goal: could be a marker, endpoint, distinct color
            if shape in {"endpoint", "anchor", "marker", "goal"} or (area > 0 and area < 300 and color in {1, 7, 8}):
                goal_candidates.append((i, obj))

        if player_candidates and goal_candidates:
            player_id, player_obj = player_candidates[0]
            goal_id, goal_obj = goal_candidates[0]

            confidence = 0.65
            hyp_id = f"goal-reach-{hashlib.md5(f'{player_id}-{goal_id}'.encode()).hexdigest()[:8]}"

            return GoalHypothesis(
                id=hyp_id,
                goal_type=GoalType.REACH_TARGET,
                claim=f"Move object {player_id} to reach target {goal_id}",
                confidence=confidence,
                status="active",
                target_object_ids=[player_id, goal_id],
                evidence_path_ids=[],
                properties={
                    "player_object_id": player_id,
                    "goal_object_id": goal_id,
                    "detector": "reach_target",
                },
            )

        return None

    def _detect_collect_or_activate(
        self,
        objects: List[Dict[str, Any]],
        world_model: Any,
    ) -> Optional[GoalHypothesis]:
        """Detect if goal is to collect objects or activate endpoints."""
        if not objects or len(objects) < 2:
            return None

        endpoint_objects = [
            (i, obj)
            for i, obj in enumerate(objects)
            if obj.get("shape_kind") in {"endpoint", "anchor", "marker"} or (obj.get("color") == 7 and obj.get("area", 0) < 100)
        ]

        collectable_objects = [
            (i, obj)
            for i, obj in enumerate(objects)
            if obj.get("shape_kind") in {"token", "ring", "portal"} or (obj.get("color") not in {0, 7} and obj.get("area", 0) < 200)
        ]

        if endpoint_objects and collectable_objects:
            confidence = 0.6
            target_ids = [i for i, _ in endpoint_objects[:3]] + [i for i, _ in collectable_objects[:2]]
            hyp_id = f"goal-collect-{hashlib.md5(str(target_ids).encode()).hexdigest()[:8]}"

            return GoalHypothesis(
                id=hyp_id,
                goal_type=GoalType.COLLECT_OR_ACTIVATE,
                claim=f"Collect or activate {len(collectable_objects)} objects at {len(endpoint_objects)} endpoints",
                confidence=confidence,
                status="active",
                target_object_ids=target_ids,
                evidence_path_ids=[],
                properties={
                    "endpoint_count": len(endpoint_objects),
                    "collectable_count": len(collectable_objects),
                    "detector": "collect_or_activate",
                },
            )

        return None

    def _detect_level_advance(
        self,
        terminal_context: Dict[str, Any],
        env_signals: Optional[Dict[str, Any]],
        world_model: Any,
    ) -> Optional[GoalHypothesis]:
        """Detect if goal is to advance to the next level."""
        levels_completed = terminal_context.get("levels_completed", 0)

        if levels_completed > 0:
            confidence = 0.9
            hyp_id = f"goal-level-advance-lvl{levels_completed}"

            return GoalHypothesis(
                id=hyp_id,
                goal_type=GoalType.LEVEL_ADVANCE,
                claim=f"Advance to next level (currently at level {levels_completed})",
                confidence=confidence,
                status="active",
                target_object_ids=[],
                evidence_path_ids=[],
                properties={
                    "current_level": levels_completed,
                    "detector": "level_advance",
                },
            )

        return None

    def _detect_endpoint_connection(
        self,
        objects: List[Dict[str, Any]],
        world_model: Any,
    ) -> Optional[GoalHypothesis]:
        """Detect if goal is to connect rings/hubs to endpoints."""
        if not objects or len(objects) < 2:
            return None

        ring_objects = [
            (i, obj)
            for i, obj in enumerate(objects)
            if obj.get("shape_kind") in {"ring", "portal", "hub"} or (obj.get("color") not in {0, 7} and obj.get("area", 50) > 20)
        ]

        endpoint_objects = [
            (i, obj)
            for i, obj in enumerate(objects)
            if obj.get("shape_kind") in {"endpoint", "anchor", "spoke"} or (obj.get("color") == 7 or obj.get("color") in {1, 8})
        ]

        if ring_objects and endpoint_objects and len(ring_objects) + len(endpoint_objects) >= 3:
            confidence = 0.7
            target_ids = [i for i, _ in ring_objects[:3]] + [i for i, _ in endpoint_objects[:2]]
            hyp_id = f"goal-endpoint-{hashlib.md5(str(target_ids).encode()).hexdigest()[:8]}"

            return GoalHypothesis(
                id=hyp_id,
                goal_type=GoalType.ENDPOINT_CONNECTION,
                claim=f"Connect {len(ring_objects)} rings/hubs to {len(endpoint_objects)} endpoints",
                confidence=confidence,
                status="active",
                target_object_ids=target_ids,
                evidence_path_ids=[],
                properties={
                    "ring_count": len(ring_objects),
                    "endpoint_count": len(endpoint_objects),
                    "detector": "endpoint_connection",
                },
            )

        return None

    @staticmethod
    def demote_hypothesis_by_contradiction(
        hypothesis: GoalHypothesis,
        contradiction_count: int = 1,
    ) -> GoalHypothesis:
        """Demote a hypothesis when terminal/level evidence contradicts it."""
        if hypothesis.status == "active" and contradiction_count >= 2:
            hypothesis.status = "demoted"
            hypothesis.confidence = max(0.0, hypothesis.confidence - 0.3)
        return hypothesis
