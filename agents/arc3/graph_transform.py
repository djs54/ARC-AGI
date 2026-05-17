"""A103 — Action As Graph Transformation.

Learn action semantics by diffing mechanic graph snapshots and storing action effects
as graph transformations with configuration ids.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TransformClass(str, Enum):
    """Classification of graph transformations."""
    CONFIGURATION_CYCLE_STEP = "configuration_cycle_step"
    ROTATION_OR_PERMUTATION = "rotation_or_permutation"
    SPOKE_ENDPOINT_SWAP = "spoke_endpoint_swap"
    LINK_REWIRE = "link_rewire"
    HUB_PHASE_CHANGE = "hub_phase_change"
    GOAL_ALIGNMENT_CHANGE = "goal_alignment_change"
    IRRELEVANT_VISUAL_CHURN = "irrelevant_visual_churn"


@dataclass
class GraphTransformation:
    """Record of how a graph changes after an action."""
    action_id: str
    step: int
    transform_class: TransformClass
    confidence: float
    before_config_hash: str
    after_config_hash: str
    affected_object_ids: List[int] = field(default_factory=list)
    changed_relation_ids: List[str] = field(default_factory=list)
    goal_relevance: float = 0.0  # How much this transformation supports goal hypotheses
    evidence_path_ids: List[str] = field(default_factory=list)
    props: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for graph storage."""
        return {
            "action_id": self.action_id,
            "step": self.step,
            "transform_class": self.transform_class.value,
            "confidence": self.confidence,
            "before_config_hash": self.before_config_hash,
            "after_config_hash": self.after_config_hash,
            "affected_object_ids": self.affected_object_ids,
            "changed_relation_ids": self.changed_relation_ids,
            "goal_relevance": self.goal_relevance,
            "evidence_path_ids": self.evidence_path_ids,
            "props": self.props,
        }


class GraphTransformationLearner:
    """Learns action semantics by diffing mechanic graph snapshots."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    def diff(
        self,
        before_snapshot: Any,  # MechanicGraphSnapshot
        after_snapshot: Any,  # MechanicGraphSnapshot
        action: Dict[str, Any],
        active_goal_hypotheses: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[GraphTransformation]:
        """Diff two mechanic graph snapshots and produce a transformation record.
        
        Args:
            before_snapshot: Previous MechanicGraphSnapshot.
            after_snapshot: Current MechanicGraphSnapshot.
            action: Action dict with action_id, step.
            active_goal_hypotheses: List of active goal hypothesis dicts.
        
        Returns:
            GraphTransformation record or None if no significant change.
        """
        if not before_snapshot or not after_snapshot:
            return None

        action_id = action.get("action_id", "unknown")
        step = action.get("step", 0)

        # 1. Identify affected objects
        affected_objects = self._find_affected_objects(before_snapshot, after_snapshot)
        if not affected_objects:
            return None

        # 2. Classify the transformation
        transform_class, confidence, props = self._classify_transformation(
            before_snapshot, after_snapshot, affected_objects
        )

        # 3. Compute goal relevance
        goal_relevance = self._compute_goal_relevance(
            transform_class, affected_objects, active_goal_hypotheses
        )

        # 4. Extract changed relations
        changed_relations = self._find_changed_relations(before_snapshot, after_snapshot)

        transformation = GraphTransformation(
            action_id=action_id,
            step=step,
            transform_class=transform_class,
            confidence=confidence,
            before_config_hash=before_snapshot.configuration_hash,
            after_config_hash=after_snapshot.configuration_hash,
            affected_object_ids=list(affected_objects),
            changed_relation_ids=changed_relations,
            goal_relevance=goal_relevance,
            evidence_path_ids=[],
            props=props,
        )

        return transformation

    def _find_affected_objects(
        self,
        before_snapshot: Any,
        after_snapshot: Any,
    ) -> set:
        """Identify objects that changed position or properties."""
        affected = set()

        # Check for moved objects
        for obj_id, before_obj in before_snapshot.objects.items():
            if obj_id not in after_snapshot.objects:
                affected.add(obj_id)
                continue

            after_obj = after_snapshot.objects[obj_id]

            # Check centroid movement
            if before_obj.centroid != after_obj.centroid:
                affected.add(obj_id)

            # Check color change
            if before_obj.color != after_obj.color:
                affected.add(obj_id)

        # Check for new objects
        for obj_id in after_snapshot.objects:
            if obj_id not in before_snapshot.objects:
                affected.add(obj_id)

        return affected

    def _classify_transformation(
        self,
        before_snapshot: Any,
        after_snapshot: Any,
        affected_objects: set,
    ) -> tuple:
        """Classify the type of transformation."""
        # If configuration hash is identical, it's likely irrelevant churn
        if before_snapshot.configuration_hash == after_snapshot.configuration_hash:
            return (
                TransformClass.IRRELEVANT_VISUAL_CHURN,
                0.4,
                {"reason": "configuration_unchanged"},
            )

        # Check for cycle patterns (configuration repeating)
        if self._is_cycle_pattern(before_snapshot, after_snapshot):
            return (
                TransformClass.CONFIGURATION_CYCLE_STEP,
                0.85,
                {"reason": "cyclic_configuration_change"},
            )

        # Check for rotation/permutation
        if self._is_rotation_pattern(before_snapshot, after_snapshot, affected_objects):
            return (
                TransformClass.ROTATION_OR_PERMUTATION,
                0.8,
                {"reason": "rotational_movement", "object_count": len(affected_objects)},
            )

        # Check for spoke/endpoint swap
        if self._is_spoke_endpoint_swap(before_snapshot, after_snapshot, affected_objects):
            return (
                TransformClass.SPOKE_ENDPOINT_SWAP,
                0.75,
                {"reason": "endpoint_reassignment"},
            )

        # Check for link rewiring
        if self._is_link_rewire(before_snapshot, after_snapshot):
            return (
                TransformClass.LINK_REWIRE,
                0.7,
                {"reason": "connection_topology_change"},
            )

        # Check for hub phase change (color, rotation, etc.)
        if self._is_hub_phase_change(before_snapshot, after_snapshot, affected_objects):
            return (
                TransformClass.HUB_PHASE_CHANGE,
                0.7,
                {"reason": "hub_state_change", "object_count": len(affected_objects)},
            )

        # Default to churn
        return (
            TransformClass.IRRELEVANT_VISUAL_CHURN,
            0.5,
            {"reason": "unclassified_change"},
        )

    def _is_cycle_pattern(self, before_snapshot: Any, after_snapshot: Any) -> bool:
        """Detect if this is a step in a finite-state cycle."""
        # Simple heuristic: if only a few objects moved and we have few relations,
        # it's likely a cycle
        before_count = len(before_snapshot.objects)
        after_count = len(after_snapshot.objects)

        if before_count != after_count:
            return False

        # If object count is small (< 10) and mostly symmetric, likely a cycle
        return before_count < 10

    def _is_rotation_pattern(
        self,
        before_snapshot: Any,
        after_snapshot: Any,
        affected_objects: set,
    ) -> bool:
        """Detect rotational or permutational movement."""
        if len(affected_objects) < 2:
            return False

        # Check if affected objects maintain relative distances but change orientation
        before_objs = [before_snapshot.objects[oid] for oid in affected_objects if oid in before_snapshot.objects]
        after_objs = [after_snapshot.objects[oid] for oid in affected_objects if oid in after_snapshot.objects]

        if len(before_objs) != len(after_objs):
            return False

        # Compute centroid of affected objects before/after
        before_centroid = self._compute_group_centroid(before_objs)
        after_centroid = self._compute_group_centroid(after_objs)

        # If centroid moved significantly, not a pure rotation
        centroid_dist = ((before_centroid[0] - after_centroid[0]) ** 2 + (before_centroid[1] - after_centroid[1]) ** 2) ** 0.5
        if centroid_dist > 5:
            return False

        # Check relative distances are preserved (rotation preserves distances)
        before_dists = self._compute_pairwise_distances(before_objs)
        after_dists = self._compute_pairwise_distances(after_objs)

        if before_dists and after_dists:
            # Check if distance distributions are similar
            return self._distances_similar(before_dists, after_dists)

        return False

    def _is_spoke_endpoint_swap(
        self,
        before_snapshot: Any,
        after_snapshot: Any,
        affected_objects: set,
    ) -> bool:
        """Detect swapping of spokes or endpoints."""
        # Look for spoke/endpoint-shaped objects moving to new positions
        before_spokes = [oid for oid in affected_objects if before_snapshot.objects.get(oid, {}).shape_kind in {"spoke", "endpoint"}]
        after_spokes = [oid for oid in affected_objects if after_snapshot.objects.get(oid, {}).shape_kind in {"spoke", "endpoint"}]

        return len(before_spokes) > 0 and len(after_spokes) > 0 and len(affected_objects) < 8

    def _is_link_rewire(self, before_snapshot: Any, after_snapshot: Any) -> bool:
        """Detect changes in connectivity (relation topology)."""
        before_relations_set = set((r.src, r.rel, r.dst) for r in before_snapshot.relations)
        after_relations_set = set((r.src, r.rel, r.dst) for r in after_snapshot.relations)

        # Check for significant changes in relations
        changed = len(before_relations_set.symmetric_difference(after_relations_set))
        return changed >= 2

    def _is_hub_phase_change(
        self,
        before_snapshot: Any,
        after_snapshot: Any,
        affected_objects: set,
    ) -> bool:
        """Detect hub state changes (phase, color, rotation)."""
        hub_objects = [oid for oid in affected_objects if before_snapshot.objects.get(oid, {}).shape_kind == "hub"]

        if not hub_objects:
            return False

        # Check if hub colors or positions changed
        for oid in hub_objects:
            if oid in before_snapshot.objects and oid in after_snapshot.objects:
                if before_snapshot.objects[oid].color != after_snapshot.objects[oid].color:
                    return True

        return len(hub_objects) > 0

    def _find_changed_relations(
        self,
        before_snapshot: Any,
        after_snapshot: Any,
    ) -> List[str]:
        """Find relations that changed between snapshots."""
        before_rels = {(r.src, r.rel, r.dst): r for r in before_snapshot.relations}
        after_rels = {(r.src, r.rel, r.dst): r for r in after_snapshot.relations}

        changed = []
        for key in before_rels:
            if key not in after_rels:
                changed.append(f"removed_{key[0]}-{key[1]}-{key[2]}")

        for key in after_rels:
            if key not in before_rels:
                changed.append(f"added_{key[0]}-{key[1]}-{key[2]}")

        return changed[:10]  # Cap to 10 changes

    def _compute_goal_relevance(
        self,
        transform_class: TransformClass,
        affected_objects: set,
        active_goal_hypotheses: Optional[List[Dict[str, Any]]],
    ) -> float:
        """Compute how much this transformation supports active goals."""
        if not active_goal_hypotheses:
            return 0.0

        relevance = 0.0

        # Configuration cycle steps support cycles in single-action games
        if transform_class == TransformClass.CONFIGURATION_CYCLE_STEP:
            relevance = 0.7

        # Endpoint/spoke swaps are usually goal-relevant
        elif transform_class == TransformClass.SPOKE_ENDPOINT_SWAP:
            relevance = 0.8

        # Link rewires could be goal-relevant if they affect target objects
        elif transform_class == TransformClass.LINK_REWIRE:
            for hyp in active_goal_hypotheses:
                target_ids = hyp.get("target_object_ids", [])
                if any(oid in affected_objects for oid in target_ids):
                    relevance = max(relevance, 0.75)

        # Hub phase changes can be goal-relevant
        elif transform_class == TransformClass.HUB_PHASE_CHANGE:
            relevance = 0.6

        # Rotations might be goal-relevant in ring/spoke games
        elif transform_class == TransformClass.ROTATION_OR_PERMUTATION:
            relevance = 0.5

        return min(1.0, relevance)

    @staticmethod
    def _compute_group_centroid(objects: List[Any]) -> tuple:
        """Compute centroid of a group of objects."""
        if not objects:
            return (0, 0)
        total_r = sum(obj.centroid[0] for obj in objects)
        total_c = sum(obj.centroid[1] for obj in objects)
        return (total_r / len(objects), total_c / len(objects))

    @staticmethod
    def _compute_pairwise_distances(objects: List[Any]) -> List[float]:
        """Compute pairwise distances between object centroids."""
        distances = []
        for i in range(len(objects)):
            for j in range(i + 1, len(objects)):
                d = ((objects[i].centroid[0] - objects[j].centroid[0]) ** 2 + (objects[i].centroid[1] - objects[j].centroid[1]) ** 2) ** 0.5
                distances.append(d)
        return sorted(distances)

    @staticmethod
    def _distances_similar(dists1: List[float], dists2: List[float], tolerance: float = 2.0) -> bool:
        """Check if two distance lists are similar."""
        if len(dists1) != len(dists2):
            return False
        for d1, d2 in zip(dists1, dists2):
            if abs(d1 - d2) > tolerance:
                return False
        return True
