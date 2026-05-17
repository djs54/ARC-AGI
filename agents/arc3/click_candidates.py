"""A107 - Graph click candidate generator.

Generates clickable coordinate candidates from mechanic graph objects and relations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ClickableCandidate:
    """A clickable coordinate candidate derived from graph objects/relations."""
    
    id: str
    x: int
    y: int
    color: int | None = None
    role: str = "unknown"  # e.g., "object_center", "framed_center", "mismatch_cell", "panel_center"
    confidence: float = 0.5
    rank: int = 0
    source_object_id: str | None = None
    panel_id: str | None = None
    goal_type: str | None = None
    evidence_path_ids: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "color": self.color,
            "role": self.role,
            "confidence": self.confidence,
            "rank": self.rank,
            "source_object_id": self.source_object_id,
            "panel_id": self.panel_id,
            "goal_type": self.goal_type,
            "evidence_path_ids": self.evidence_path_ids,
        }


class ClickCandidateGenerator:
    """Generates bounded clickable candidates from mechanic graph."""
    
    MAX_CANDIDATES_PER_FRAME = 32
    MAX_EVIDENCE_PATH_IDS = 6
    
    def __init__(self, world_model: Any):
        """Initialize generator with world model reference."""
        self.world_model = world_model
    
    def generate(
        self,
        mechanic_graph_snapshot: Optional[Dict[str, Any]] = None,
        active_goal_hypotheses: Optional[List[Dict[str, Any]]] = None,
        limit: int = 32
    ) -> List[ClickableCandidate]:
        """Generate clickable candidates from graph state.
        
        Args:
            mechanic_graph_snapshot: Optional mechanic graph snapshot
            active_goal_hypotheses: Active goal hypotheses to drive candidate selection
            limit: Maximum candidates to return (capped at MAX_CANDIDATES_PER_FRAME)
        
        Returns:
            List of ranked ClickableCandidate records
        """
        limit = min(limit, self.MAX_CANDIDATES_PER_FRAME)
        candidates: Dict[Tuple[int, int], ClickableCandidate] = {}
        mechanic_graph_snapshot = self._normalize_snapshot(mechanic_graph_snapshot)
        
        # Extract candidates from active goal hypotheses
        if active_goal_hypotheses:
            for hyp in active_goal_hypotheses:
                hyp_candidates = self._extract_from_goal_hypothesis(hyp)
                for cand in hyp_candidates:
                    key = (cand.x, cand.y)
                    if key not in candidates or cand.confidence > candidates[key].confidence:
                        candidates[key] = cand
        
        # Extract candidates from mechanic graph objects
        if mechanic_graph_snapshot:
            object_candidates = self._extract_from_mechanic_objects(mechanic_graph_snapshot)
            for cand in object_candidates:
                key = (cand.x, cand.y)
                if key not in candidates or cand.confidence > candidates[key].confidence:
                    candidates[key] = cand
        
        # Rank and return bounded list
        ranked = sorted(candidates.values(), key=lambda c: (-c.confidence, c.rank))
        return ranked[:limit]

    def _normalize_snapshot(self, snapshot: Any) -> Dict[str, Any]:
        """Accept either dict snapshots or A102 MechanicGraphSnapshot objects."""
        if not snapshot:
            return {}
        if isinstance(snapshot, dict):
            return snapshot

        objects = []
        object_centers: Dict[str, Dict[str, Any]] = {}
        for obj in (getattr(snapshot, "objects", {}) or {}).values():
            centroid = getattr(obj, "centroid", None) or (0, 0)
            bbox = getattr(obj, "bbox", None) or (0, 0, 0, 0)
            try:
                center_y = int(round(float(centroid[0])))
                center_x = int(round(float(centroid[1])))
            except Exception:
                center_y = int((bbox[0] + bbox[2]) / 2)
                center_x = int((bbox[1] + bbox[3]) / 2)
            shape_kind = str(getattr(obj, "shape_kind", "") or "")
            props = getattr(obj, "props", {}) or {}
            objects.append({
                "id": str(getattr(obj, "id", "unknown")),
                "center_x": center_x,
                "center_y": center_y,
                "color": getattr(obj, "color", None),
                "confidence": getattr(obj, "confidence", 0.5),
                "is_framed": shape_kind in {"ring", "frame", "target"} or bool(props.get("is_framed")),
                "is_target": shape_kind in {"ring", "endpoint"} or bool(props.get("is_target")),
            })
            object_centers[str(getattr(obj, "id", "unknown"))] = {
                "x": center_x,
                "y": center_y,
                "color": getattr(obj, "color", None),
            }

        relations = []
        for rel in getattr(snapshot, "relations", []) or []:
            rel_props = getattr(rel, "props", {}) or {}
            dst_key = str(getattr(rel, "dst", ""))
            dst_center = object_centers.get(dst_key, {})
            target_x = rel_props.get("target_x")
            target_y = rel_props.get("target_y")
            if target_x is None:
                target_x = dst_center.get("x")
            if target_y is None:
                target_y = dst_center.get("y")
            relations.append({
                "id": f"rel-{getattr(rel, 'src', 'x')}-{getattr(rel, 'rel', 'rel')}-{getattr(rel, 'dst', 'y')}",
                "type": getattr(rel, "rel", "unknown"),
                "target_x": target_x,
                "target_y": target_y,
                "color": rel_props.get("color", dst_center.get("color")),
            })

        return {"objects": objects, "relations": relations, "panels": []}
    
    def _extract_from_goal_hypothesis(self, hypothesis: Dict[str, Any]) -> List[ClickableCandidate]:
        """Extract candidates from an active goal hypothesis."""
        candidates = []
        goal_id = hypothesis.get("id", "unknown")
        goal_type = hypothesis.get("goal_type", "unknown")
        
        # For color_correspondence goals, extract from target objects
        if goal_type == "color_correspondence":
            if "target_objects" in hypothesis:
                for obj in hypothesis["target_objects"]:
                    cand = ClickableCandidate(
                        id=f"click-goal-{goal_id}-{obj.get('id', '')}",
                        x=obj.get("center_x", 0),
                        y=obj.get("center_y", 0),
                        color=obj.get("color"),
                        role="goal_target_center",
                        confidence=0.7,
                        source_object_id=obj.get("id"),
                        goal_type=goal_type,
                        evidence_path_ids=[goal_id],
                    )
                    candidates.append(cand)
        
        # For pattern_completion, extract from mismatch/missing locations
        elif goal_type == "pattern_completion":
            if "mismatch_locations" in hypothesis:
                for i, loc in enumerate(hypothesis["mismatch_locations"]):
                    cand = ClickableCandidate(
                        id=f"click-goal-{goal_id}-mismatch-{i}",
                        x=loc.get("x", 0),
                        y=loc.get("y", 0),
                        color=loc.get("expected_color"),
                        role="mismatch_cell",
                        confidence=0.6,
                        goal_type=goal_type,
                        evidence_path_ids=[goal_id],
                    )
                    candidates.append(cand)
        
        return candidates
    
    def _extract_from_mechanic_objects(self, snapshot: Dict[str, Any]) -> List[ClickableCandidate]:
        """Extract candidates from mechanic graph objects."""
        candidates = []
        
        # Extract from objects field
        for obj in snapshot.get("objects", []):
            obj_id = obj.get("id", "unknown")
            
            # Object centroid
            if obj.get("center_x") is not None and obj.get("center_y") is not None:
                cand = ClickableCandidate(
                    id=f"click-obj-{obj_id}-center",
                    x=int(obj["center_x"]),
                    y=int(obj["center_y"]),
                    color=obj.get("color"),
                    role="object_center",
                    confidence=0.5,
                    source_object_id=obj_id,
                    evidence_path_ids=[obj_id],
                )
                candidates.append(cand)
            
            # Framed target center (high confidence)
            if obj.get("is_framed") or obj.get("is_target"):
                if obj.get("center_x") is not None and obj.get("center_y") is not None:
                    cand = ClickableCandidate(
                        id=f"click-obj-{obj_id}-framed",
                        x=int(obj["center_x"]),
                        y=int(obj["center_y"]),
                        color=obj.get("color"),
                        role="framed_center",
                        confidence=0.8,
                        source_object_id=obj_id,
                        evidence_path_ids=[obj_id],
                    )
                    candidates.append(cand)
        
        # Extract from relations endpoints
        for rel in snapshot.get("relations", []):
            rel_id = rel.get("id", "unknown")
            rel_type = rel.get("type", "unknown")
            
            # High-confidence relation endpoints
            if rel_type in {"MISMATCH", "MATCHES_COLOR", "CANDIDATE_TARGET"}:
                if rel.get("target_x") is not None and rel.get("target_y") is not None:
                    confidence = 0.7 if rel_type == "CANDIDATE_TARGET" else 0.6
                    cand = ClickableCandidate(
                        id=f"click-rel-{rel_id}",
                        x=int(rel["target_x"]),
                        y=int(rel["target_y"]),
                        color=rel.get("color"),
                        role=f"{rel_type.lower()}_endpoint",
                        confidence=confidence,
                        evidence_path_ids=[rel_id],
                    )
                    candidates.append(cand)
        
        # Extract from panels if available
        for panel in snapshot.get("panels", []):
            panel_id = panel.get("id", "unknown")
            if panel.get("center_x") is not None and panel.get("center_y") is not None:
                cand = ClickableCandidate(
                    id=f"click-panel-{panel_id}-center",
                    x=int(panel["center_x"]),
                    y=int(panel["center_y"]),
                    role="panel_center",
                    confidence=0.4,
                    panel_id=panel_id,
                    evidence_path_ids=[panel_id],
                )
                candidates.append(cand)
        
        return candidates


class ClickCandidateStore:
    """Store and query clickable candidates in the world model."""
    
    def __init__(self):
        """Initialize candidate store."""
        self.candidates_by_frame: Dict[str, List[ClickableCandidate]] = {}
        self.candidate_nodes: Dict[str, Dict[str, Any]] = {}
    
    def upsert_candidates(self, frame_hash: str, candidates: List[ClickableCandidate]) -> None:
        """Store candidates for a frame."""
        self.candidates_by_frame[frame_hash] = candidates
        
        # Also index by candidate id
        for cand in candidates:
            self.candidate_nodes[cand.id] = cand.to_dict()
    
    def get_candidates(self, frame_hash: str, goal_type: Optional[str] = None, limit: int = 16) -> List[Dict[str, Any]]:
        """Retrieve candidates for a frame, optionally filtered by goal type."""
        candidates = self.candidates_by_frame.get(frame_hash, [])
        
        if goal_type:
            candidates = [c for c in candidates if c.goal_type == goal_type]
        
        return [c.to_dict() for c in candidates[:limit]]
    
    def get_candidate_by_id(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve candidate by id."""
        return self.candidate_nodes.get(candidate_id)
