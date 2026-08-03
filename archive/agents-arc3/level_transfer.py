"""A105 — Cross-Level Mechanic Transfer From Solved Relations.

Compile solved-level evidence into compact graph templates and reuse those templates
across later levels in the same game and aggregate memory.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LevelSolutionTemplate:
    """Compact template of a solved level for transfer to next level."""
    id: str
    game_id: str
    level_index: int
    goal_type: str
    goal_relation_signature: str  # Compact signature of goal relation
    mechanic_signature: str  # Compact signature of mechanic graph
    action_transform_signature: str  # Compact signature of action effects
    confidence: float
    evidence_path_ids: List[str] = field(default_factory=list)
    props: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "game_id": self.game_id,
            "level_index": self.level_index,
            "goal_type": self.goal_type,
            "goal_relation_signature": self.goal_relation_signature,
            "mechanic_signature": self.mechanic_signature,
            "action_transform_signature": self.action_transform_signature,
            "confidence": self.confidence,
            "evidence_path_ids": self.evidence_path_ids,
            "props": self.props,
        }


class LevelTransferCompiler:
    """Compiles solved level evidence into templates for transfer."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    def compile_on_level_advance(
        self,
        game_id: str,
        level_index: int,
        active_goal_hypothesis: Optional[Dict[str, Any]],
        mechanic_graph_snapshot: Optional[Any],
        action_transformations: Optional[List[Dict[str, Any]]],
        cycle_evidence: Optional[Dict[str, Any]],
    ) -> Optional[LevelSolutionTemplate]:
        """Compile a solution template when a level is solved.
        
        Args:
            game_id: Identifier for the game.
            level_index: Index of the solved level.
            active_goal_hypothesis: The goal hypothesis that led to success.
            mechanic_graph_snapshot: The mechanic graph at level completion.
            action_transformations: List of action transformation records.
            cycle_evidence: Optional cycle search evidence from A104.
        
        Returns:
            LevelSolutionTemplate or None if insufficient evidence.
        """
        if not active_goal_hypothesis or not mechanic_graph_snapshot:
            return None

        # 1. Create signatures from components
        goal_sig = self._signature_from_goal_hypothesis(active_goal_hypothesis)
        mechanic_sig = self._signature_from_mechanic_graph(mechanic_graph_snapshot)
        action_sig = self._signature_from_transformations(action_transformations or [])

        # 2. Compute template confidence
        confidence = self._compute_template_confidence(
            active_goal_hypothesis,
            action_transformations,
            cycle_evidence,
        )

        # 3. Create template id
        template_id = self._create_template_id(game_id, level_index, goal_sig, mechanic_sig)

        # 4. Collect evidence paths
        evidence_ids = []
        if isinstance(active_goal_hypothesis, dict):
            evidence_ids.extend(active_goal_hypothesis.get("evidence_path_ids", [])[:3])

        props = {
            "goal_type": active_goal_hypothesis.get("goal_type", "unknown"),
            "action_count": len(action_transformations or []),
            "cycle_closed": cycle_evidence.get("configuration_cycle_closed", False) if cycle_evidence else False,
        }

        template = LevelSolutionTemplate(
            id=template_id,
            game_id=game_id,
            level_index=level_index,
            goal_type=active_goal_hypothesis.get("goal_type", "unknown"),
            goal_relation_signature=goal_sig,
            mechanic_signature=mechanic_sig,
            action_transform_signature=action_sig,
            confidence=confidence,
            evidence_path_ids=evidence_ids,
            props=props,
        )

        return template

    @staticmethod
    def _signature_from_goal_hypothesis(hyp: Dict[str, Any]) -> str:
        """Extract signature from goal hypothesis."""
        components = [
            hyp.get("goal_type", "unknown"),
            str(hyp.get("confidence", 0.0))[0:3],  # First 3 chars of confidence
        ]
        return hashlib.md5("_".join(components).encode()).hexdigest()[:12]

    @staticmethod
    def _signature_from_mechanic_graph(snapshot: Any) -> str:
        """Extract signature from mechanic graph snapshot."""
        # Use the configuration_hash if available
        if hasattr(snapshot, "configuration_hash"):
            return snapshot.configuration_hash[:16]

        # Fallback: create from object count and relation count
        obj_count = len(snapshot.objects) if hasattr(snapshot, "objects") else 0
        rel_count = len(snapshot.relations) if hasattr(snapshot, "relations") else 0
        sig_str = f"graph_{obj_count}objs_{rel_count}rels"
        return hashlib.md5(sig_str.encode()).hexdigest()[:12]

    @staticmethod
    def _signature_from_transformations(transformations: List[Dict[str, Any]]) -> str:
        """Extract signature from action transformations."""
        if not transformations:
            return "no_transforms"

        classes = [t.get("transform_class", "unknown") for t in transformations[:5]]
        sig_str = "_".join(classes)
        return hashlib.md5(sig_str.encode()).hexdigest()[:12]

    @staticmethod
    def _compute_template_confidence(
        goal_hypothesis: Dict[str, Any],
        action_transformations: Optional[List[Dict[str, Any]]],
        cycle_evidence: Optional[Dict[str, Any]],
    ) -> float:
        """Compute confidence of the template."""
        confidence = goal_hypothesis.get("confidence", 0.5)

        # Boost if we have good action transformation evidence
        if action_transformations and len(action_transformations) >= 2:
            avg_transform_conf = sum(t.get("confidence", 0.5) for t in action_transformations) / len(action_transformations)
            confidence = (confidence + avg_transform_conf) / 2

        # Slight boost if cycle search succeeded
        if cycle_evidence and not cycle_evidence.get("configuration_cycle_closed_unsolved", False):
            confidence = min(0.99, confidence + 0.1)

        return min(0.99, max(0.0, confidence))

    @staticmethod
    def _create_template_id(
        game_id: str,
        level_index: int,
        goal_sig: str,
        mechanic_sig: str,
    ) -> str:
        """Create a unique template id."""
        components = [game_id, str(level_index), goal_sig, mechanic_sig]
        hash_val = hashlib.md5("_".join(components).encode()).hexdigest()[:8]
        return f"lvl-template-{hash_val}"


class LevelTransferMatcher:
    """Matches current level to stored solution templates."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    def match(
        self,
        current_mechanic_graph: Optional[Any],
        active_goal_hypotheses: Optional[List[Dict[str, Any]]],
        templates: Optional[List[Dict[str, Any]]],
    ) -> Optional[Dict[str, Any]]:
        """Try to match current level to a template.
        
        Args:
            current_mechanic_graph: Current MechanicGraphSnapshot.
            active_goal_hypotheses: Active goal hypotheses for current level.
            templates: Available level solution templates.
        
        Returns:
            Matched template with score, or None if no good match.
        """
        if not templates or not active_goal_hypotheses:
            return None

        best_match = None
        best_score = 0.0

        current_mechanic_sig = self._signature_from_mechanic_graph(current_mechanic_graph) if current_mechanic_graph else ""
        current_goal_types = {hyp.get("goal_type", "unknown") for hyp in active_goal_hypotheses}

        for template in templates:
            # Score based on goal type match
            template_goal_type = template.get("goal_type", "unknown")
            goal_type_match = 1.0 if template_goal_type in current_goal_types else 0.3

            # Score based on mechanic signature match
            mechanic_match = 0.5  # Default if no current graph
            if current_mechanic_sig and template.get("mechanic_signature") == current_mechanic_sig:
                mechanic_match = 1.0
            elif current_mechanic_sig:
                # Partial match based on signature similarity
                mechanic_match = self._signature_similarity(current_mechanic_sig, template.get("mechanic_signature", ""))

            # Combined score
            score = (goal_type_match * 0.5 + mechanic_match * 0.5) * template.get("confidence", 0.5)

            if score > best_score:
                best_score = score
                best_match = {
                    "template": template,
                    "match_score": score,
                    "goal_type_match": goal_type_match,
                    "mechanic_match": mechanic_match,
                }

        # Return match only if reasonably confident
        if best_match and best_score > 0.4:
            return best_match

        return None

    @staticmethod
    def _signature_from_mechanic_graph(snapshot: Any) -> str:
        """Extract signature from mechanic graph."""
        if hasattr(snapshot, "configuration_hash"):
            return snapshot.configuration_hash[:16]
        return ""

    @staticmethod
    def _signature_similarity(sig1: str, sig2: str) -> float:
        """Compute similarity between two signatures."""
        if not sig1 or not sig2:
            return 0.0

        # Simple Hamming-distance based similarity on hex strings
        matching_chars = sum(1 for c1, c2 in zip(sig1, sig2) if c1 == c2)
        return matching_chars / max(len(sig1), len(sig2))
