"""A102 — Object Mechanic Graph Extraction.

Build a deterministic object mechanic graph from each frame so action evaluation
can operate on objects and relations instead of raw pixel diffs.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class MechanicObject:
    """An object in the mechanic graph with stable properties."""
    id: int
    signature: str  # Stable hash across frames
    color: int
    shape_kind: str  # "ring", "hub", "endpoint", "spoke", "terrain"
    bbox: Tuple[int, int, int, int]  # (min_r, min_c, max_r, max_c)
    centroid: Tuple[float, float]
    area: int
    confidence: float
    props: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MechanicRelation:
    """A relationship between objects in the mechanic graph."""
    src: int  # object id
    rel: str  # "MATCHES_COLOR", "CONNECTED_TO", "NEAR", "INSIDE_OR_OVERLAPS", "CANDIDATE_TARGET", "ANCHORS"
    dst: int  # object id
    confidence: float
    evidence_path_ids: List[str] = field(default_factory=list)
    props: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MechanicGraphSnapshot:
    """A snapshot of the mechanic graph at a particular frame."""
    step: int
    frame_hash: str
    objects: Dict[int, MechanicObject] = field(default_factory=dict)
    relations: List[MechanicRelation] = field(default_factory=list)
    configuration_hash: str = ""  # Hash representing overall configuration
    timestamp: float = 0.0


class MechanicGraphExtractor:
    """Deterministic extractor for object mechanic graphs from grids."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self._object_signatures: Dict[str, int] = {}  # signature -> stable id
        self._next_object_id = 0

    def extract(
        self,
        grid: List[List[int]],
        frame_hash: str,
        step: int,
        scene_objects: Optional[List[Dict[str, Any]]] = None,
    ) -> MechanicGraphSnapshot:
        """Extract mechanic graph snapshot from grid and scene objects.
        
        Args:
            grid: Current game grid as 2D list of color values.
            frame_hash: Hash of the frame for deduplication.
            step: Current step in the game.
            scene_objects: Optional pre-computed scene objects from scene_graph.
        
        Returns:
            MechanicGraphSnapshot with objects and relations.
        """
        snapshot = MechanicGraphSnapshot(step=step, frame_hash=frame_hash)

        # If no scene objects provided, use the grid directly
        if scene_objects is None:
            scene_objects = self._objects_from_grid(grid)

        # 1. Create mechanic objects with shape classification
        for i, obj in enumerate(scene_objects):
            if not obj:
                continue

            obj_id = self._get_stable_object_id(obj)
            shape_kind = self._classify_shape(obj)
            color = obj.get("color", 0)
            bbox = tuple(obj.get("bbox", (0, 0, 1, 1)))
            centroid = tuple(obj.get("centroid", (0.5, 0.5)))
            area = obj.get("area", 1)

            signature = self._compute_object_signature(obj, shape_kind, color)

            mech_obj = MechanicObject(
                id=obj_id,
                signature=signature,
                color=color,
                shape_kind=shape_kind,
                bbox=bbox,
                centroid=centroid,
                area=area,
                confidence=0.8,
                props={
                    "location_hint": obj.get("location_hint", "unknown"),
                    "scene_node_id": i,
                },
            )
            snapshot.objects[obj_id] = mech_obj

        # 2. Extract relations
        relations = self._extract_relations(snapshot.objects, grid)
        snapshot.relations = relations

        # 3. Compute configuration hash
        snapshot.configuration_hash = self._compute_configuration_hash(snapshot)

        return snapshot

    def _objects_from_grid(self, grid: List[List[int]]) -> List[Dict[str, Any]]:
        """Extract objects from grid by connected components (fallback)."""
        if not grid or not grid[0]:
            return []

        rows, cols = len(grid), len(grid[0])
        visited: Set[Tuple[int, int]] = set()
        objects: List[Dict[str, Any]] = []

        for r in range(rows):
            for c in range(cols):
                if (r, c) in visited or grid[r][c] == 0:
                    continue

                # BFS to find connected component
                component = self._bfs_component(grid, r, c, visited, rows, cols)
                if component:
                    obj_dict = self._component_to_object(component, grid)
                    objects.append(obj_dict)

        return objects

    def _bfs_component(
        self,
        grid: List[List[int]],
        start_r: int,
        start_c: int,
        visited: Set[Tuple[int, int]],
        rows: int,
        cols: int,
    ) -> List[Tuple[int, int]]:
        """BFS to extract a connected component."""
        component = []
        color = grid[start_r][start_c]
        queue = [(start_r, start_c)]
        visited.add((start_r, start_c))

        while queue:
            r, c = queue.pop(0)
            component.append((r, c))

            for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                nr, nc = r + dr, c + dc
                if 0 <= nr < rows and 0 <= nc < cols and (nr, nc) not in visited and grid[nr][nc] == color:
                    visited.add((nr, nc))
                    queue.append((nr, nc))

        return component

    def _component_to_object(self, component: List[Tuple[int, int]], grid: List[List[int]]) -> Dict[str, Any]:
        """Convert a connected component to an object dict."""
        if not component:
            return {}

        rows = [r for r, c in component]
        cols = [c for r, c in component]
        min_r, max_r = min(rows), max(rows)
        min_c, max_c = min(cols), max(cols)

        centroid = (
            sum(r for r, c in component) / len(component),
            sum(c for r, c in component) / len(component),
        )

        color = grid[component[0][0]][component[0][1]] if component else 0
        area = len(component)

        return {
            "color": color,
            "bbox": (min_r, min_c, max_r, max_c),
            "centroid": centroid,
            "area": area,
            "cells": component,
        }

    def _get_stable_object_id(self, obj: Dict[str, Any]) -> int:
        """Get or create a stable object ID."""
        sig = self._compute_object_signature(obj, obj.get("shape_kind", "unknown"), obj.get("color", 0))
        if sig not in self._object_signatures:
            self._object_signatures[sig] = self._next_object_id
            self._next_object_id += 1
        return self._object_signatures[sig]

    @staticmethod
    def _compute_object_signature(obj: Dict[str, Any], shape_kind: str, color: int) -> str:
        """Compute a stable signature for an object."""
        bbox = obj.get("bbox", (0, 0, 1, 1))
        area = obj.get("area", 0)
        components = [shape_kind, str(color), str(area), str(bbox[2] - bbox[0]), str(bbox[3] - bbox[1])]
        return hashlib.md5("_".join(components).encode()).hexdigest()[:12]

    @staticmethod
    def _classify_shape(obj: Dict[str, Any]) -> str:
        """Classify object shape from geometry and properties."""
        area = obj.get("area", 0)
        bbox = obj.get("bbox", (0, 0, 1, 1))
        color = obj.get("color", 0)

        height = bbox[2] - bbox[0] + 1
        width = bbox[3] - bbox[1] + 1
        aspect_ratio = width / height if height > 0 else 1.0

        # Heuristics for shape classification
        if area < 20 and color in {1, 7, 8}:
            return "endpoint"
        elif 20 <= area < 100 and 0.7 < aspect_ratio < 1.3:
            return "hub"
        elif area < 50 and (aspect_ratio > 2 or aspect_ratio < 0.5):
            return "spoke"
        elif 50 <= area < 300 and 0.7 < aspect_ratio < 1.3:
            return "ring"
        elif area > 300:
            return "terrain"
        else:
            return "portal"

    def _extract_relations(
        self,
        objects: Dict[int, MechanicObject],
        grid: List[List[int]],
    ) -> List[MechanicRelation]:
        """Extract relations between objects."""
        relations: List[MechanicRelation] = []
        obj_list = list(objects.values())

        for i, obj_a in enumerate(obj_list):
            for obj_b in obj_list[i + 1 :]:
                # MATCHES_COLOR
                if obj_a.color == obj_b.color and obj_a.color != 0:
                    relations.append(
                        MechanicRelation(
                            src=obj_a.id,
                            rel="MATCHES_COLOR",
                            dst=obj_b.id,
                            confidence=0.95,
                            props={"color": obj_a.color},
                        )
                    )

                # NEAR (adjacency based on centroid distance)
                dist = self._centroid_distance(obj_a.centroid, obj_b.centroid)
                if dist < 10:
                    relations.append(
                        MechanicRelation(
                            src=obj_a.id,
                            rel="NEAR",
                            dst=obj_b.id,
                            confidence=max(0.6, 1.0 - (dist / 10.0)),
                            props={"distance": dist},
                        )
                    )

                # INSIDE_OR_OVERLAPS
                if self._bboxes_overlap(obj_a.bbox, obj_b.bbox):
                    relations.append(
                        MechanicRelation(
                            src=obj_a.id,
                            rel="INSIDE_OR_OVERLAPS",
                            dst=obj_b.id,
                            confidence=0.85,
                            props={},
                        )
                    )

                # CANDIDATE_TARGET (endpoint-like is target for other objects)
                if obj_a.shape_kind == "endpoint" or obj_b.shape_kind == "endpoint":
                    target = obj_a if obj_a.shape_kind == "endpoint" else obj_b
                    other = obj_b if obj_a.shape_kind == "endpoint" else obj_a
                    relations.append(
                        MechanicRelation(
                            src=other.id,
                            rel="CANDIDATE_TARGET",
                            dst=target.id,
                            confidence=0.75,
                            props={},
                        )
                    )

                # ANCHORS (spoke/hub to ring relationship)
                if (obj_a.shape_kind in {"spoke", "hub"} and obj_b.shape_kind == "ring") or \
                   (obj_a.shape_kind == "ring" and obj_b.shape_kind in {"spoke", "hub"}):
                    src = obj_a if obj_a.shape_kind in {"spoke", "hub"} else obj_b
                    dst = obj_b if obj_a.shape_kind in {"spoke", "hub"} else obj_a
                    relations.append(
                        MechanicRelation(
                            src=src.id,
                            rel="ANCHORS",
                            dst=dst.id,
                            confidence=0.8,
                            props={},
                        )
                    )

        return relations

    @staticmethod
    def _centroid_distance(c1: Tuple[float, float], c2: Tuple[float, float]) -> float:
        """Compute Euclidean distance between centroids."""
        return ((c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2) ** 0.5

    @staticmethod
    def _bboxes_overlap(bbox1: Tuple[int, int, int, int], bbox2: Tuple[int, int, int, int]) -> bool:
        """Check if two bounding boxes overlap."""
        min_r1, min_c1, max_r1, max_c1 = bbox1
        min_r2, min_c2, max_r2, max_c2 = bbox2
        return not (max_r1 < min_r2 or max_r2 < min_r1 or max_c1 < min_c2 or max_c2 < min_c1)

    @staticmethod
    def _compute_configuration_hash(snapshot: MechanicGraphSnapshot) -> str:
        """Compute hash of the overall configuration."""
        config_str = ""
        for obj_id in sorted(snapshot.objects.keys()):
            obj = snapshot.objects[obj_id]
            config_str += f"{obj_id}:{obj.signature}:{obj.color};"
        for rel in sorted(snapshot.relations, key=lambda r: (r.src, r.rel, r.dst)):
            config_str += f"{rel.src}-{rel.rel}-{rel.dst};"
        return hashlib.md5(config_str.encode()).hexdigest()[:16]

    def get_compact_summary(
        self,
        snapshot: MechanicGraphSnapshot,
        max_objects: int = 12,
        max_edges: int = 24,
    ) -> Dict[str, Any]:
        """Generate a compact prompt summary of the mechanic graph."""
        objects_list = list(snapshot.objects.values())[:max_objects]
        relations_list = snapshot.relations[:max_edges]

        objects_summary = []
        for obj in objects_list:
            objects_summary.append({
                "id": obj.id,
                "shape": obj.shape_kind,
                "color": obj.color,
                "area": obj.area,
            })

        relations_summary = []
        for rel in relations_list:
            relations_summary.append({
                "from": rel.src,
                "type": rel.rel,
                "to": rel.dst,
                "confidence": rel.confidence,
            })

        return {
            "configuration_hash": snapshot.configuration_hash,
            "object_count": len(snapshot.objects),
            "relation_count": len(snapshot.relations),
            "objects": objects_summary,
            "relations": relations_summary,
        }
