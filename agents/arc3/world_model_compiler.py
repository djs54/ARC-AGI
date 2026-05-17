"""ARC World Model Compiler (A074).

Transforms raw step telemetry into structured causal claims for the World Model Graph.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

@dataclass
class CompiledClaim:
    """Base class for a causal claim derived from telemetry."""
    step: int
    kind: str
    props: Dict[str, Any]

@dataclass
class ActionEffectClaim(CompiledClaim):
    action_id: str
    effect_class: str  # no_op | pixel_churn | object_progress | terminal_progress | harmful | cycle
    terminal_alignment: str = "unknown"  # A092: terminal_aligned | local_only | regressing | oscillating | delayed_effect_pending

@dataclass
class CompiledWorldDelta:
    """Batch of claims from a single step."""
    step: int
    claims: List[CompiledClaim] = field(default_factory=list)
    failure_signal: Optional[str] = None

class WorldModelCompiler:
    """Classifies telemetry and emits causal claims."""

    def __init__(self):
        # A092: Track goal distance history for terminal alignment
        self._goal_distance_window = []  # [(step, distance), ...]
        self._max_history = 5  # Rolling window for trend detection
        # A096: Track previous goal distance for delta computation
        self._previous_goal_distance: Optional[float] = None

    def compile_step(
        self,
        step: int,
        prev_hash: Optional[str],
        curr_hash: str,
        action: Dict[str, Any],
        reward_components: Dict[str, Any],
        terminal_trend: str,
        object_progress: Dict[str, Any],
        available_actions: List[str],
        goal_distance: Optional[float] = None  # A092: current goal distance
    ) -> CompiledWorldDelta:
        delta = CompiledWorldDelta(step=step)
        action_id = action.get("action_id", "unknown")
        
        # A096: Compute goal distance delta
        goal_distance_before = self._previous_goal_distance
        goal_distance_after = goal_distance
        goal_distance_delta: Optional[float] = None
        distance_trend = "unknown"
        
        if goal_distance_before is not None and goal_distance_after is not None:
            goal_distance_delta = goal_distance_after - goal_distance_before
            if goal_distance_delta < -0.01:  # Small negative threshold for floating-point tolerance
                distance_trend = "improving"
            elif goal_distance_delta > 0.01:
                distance_trend = "regressing"
            else:
                distance_trend = "flat"
        
        # A092: Track goal distance for terminal alignment
        if goal_distance is not None:
            self._goal_distance_window.append((step, goal_distance))
            if len(self._goal_distance_window) > self._max_history:
                self._goal_distance_window.pop(0)
        
        # A096: Update previous distance for next step
        if goal_distance is not None:
            self._previous_goal_distance = goal_distance
        
        # 1. Determine effect class
        effect_class = self._classify_effect(
            prev_hash,
            curr_hash,
            reward_components,
            terminal_trend,
            object_progress,
            distance_trend,
        )
        
        # A092: Determine terminal alignment
        terminal_alignment = self._classify_terminal_alignment(
            effect_class,
            terminal_trend,
            goal_distance,
            reward_components.get("delayed_effect_pending", False)
        )
        
        # 2. Check for cycles
        if effect_class == "no_op" and prev_hash == curr_hash:
             # Already covered by classify but can be more specific
             pass
        
        # 3. Create ActionEffect claim with A092 terminal_alignment and A096 distance deltas
        delta.claims.append(ActionEffectClaim(
            step=step,
            kind="action_effect",
            action_id=action_id,
            effect_class=effect_class,
            terminal_alignment=terminal_alignment,  # A092
            props={
                "prev_hash": prev_hash,
                "curr_hash": curr_hash,
                "meaningful": reward_components.get("meaningful_progress", False),
                "terminal_aligned": terminal_alignment in ("terminal_aligned", "delayed_effect_pending"),  # A092
                "terminal_alignment": terminal_alignment,
                "terminal_trend": terminal_trend,
                "object_score": object_progress.get("score", 0.0),
                "goal_distance": goal_distance,  # A092
                # A096: Terminal distance delta fields
                "goal_distance_before": goal_distance_before,
                "goal_distance_after": goal_distance_after,
                "goal_distance_delta": goal_distance_delta,
                "distance_trend": distance_trend
            }
        ))
        
        # 4. Check for single action terminal stall
        if len(available_actions) == 1 and effect_class in ("no_op", "pixel_churn") and terminal_trend in ("flat", "regressing"):
             # This is a strong signal for a stall
             delta.failure_signal = "single_action_terminal_stall"
             
        return delta

    def _classify_terminal_alignment(
        self,
        effect_class: str,
        terminal_trend: str,
        goal_distance: Optional[float],
        delayed_effect_pending: bool
    ) -> str:
        """A092: Classify whether object progress aligns with terminal/goal improvement."""
        # Non-progress effects have no alignment
        if effect_class not in ("object_progress", "terminal_progress"):
            return "unknown"
        
        # If terminal distance is improving, it's terminal-aligned
        if terminal_trend == "improving":
            return "terminal_aligned"
        
        # If delayed effect is pending, mark as such
        if delayed_effect_pending:
            return "delayed_effect_pending"
        
        # If terminal distance is oscillating, mark as oscillating
        if terminal_trend == "oscillating":
            return "oscillating"
        
        # If terminal distance is regressing despite object progress, mark as regressing
        if terminal_trend == "regressing":
            return "regressing"
        
        # If terminal distance is flat (no improvement), it's local-only
        if terminal_trend == "flat":
            return "local_only"
        
        return "unknown"

    def _classify_effect(
        self,
        prev_hash: Optional[str],
        curr_hash: str,
        reward_components: Dict[str, Any],
        terminal_trend: str,
        object_progress: Dict[str, Any],
        distance_trend: str = "unknown",
    ) -> str:
        if prev_hash == curr_hash:
            return "no_op"

        if terminal_trend == "regressing" and reward_components.get("meaningful_progress", False):
            return "harmful"

        if reward_components.get("meaningful_progress", False):
            if reward_components.get("progress_class") == "terminal" or terminal_trend == "improving":
                return "terminal_progress"
            if reward_components.get("progress_class") == "object_monotonic" or object_progress.get("score", 0.0) > 0:
                return "object_progress"
            return "meaningful_progress"

        if distance_trend == "improving":
            return "distance_improving_move"
        if distance_trend == "regressing":
            return "distance_regressing_move"
        if terminal_trend == "regressing":
            return "harmful"
        if reward_components.get("state_transition", False) or reward_components.get("state_changed", False):
            return "state_transition"
        if distance_trend == "flat" and prev_hash and curr_hash and prev_hash != curr_hash:
            return "reversible_movement"
        if prev_hash and curr_hash and prev_hash != curr_hash:
            return "pixel_churn"

        return "visual_churn"

    # ── A103: Graph Transformation Support ──────────────────────────────

    def apply_graph_transformation(
        self,
        transformation_record: Any,
        compiled_delta: CompiledWorldDelta,
    ) -> None:
        """A103: Upgrade reversible_movement classifications when graph transformations exist.
        
        If a transformation with high confidence exists, upgrade effect classifications
        from reversible_movement to configuration_cycle_step or other specific types.
        
        Args:
            transformation_record: GraphTransformation object/dict with transform_class.
            compiled_delta: CompiledWorldDelta to update in-place.
        """
        if not transformation_record or not compiled_delta:
            return
        
        # Extract transform class
        trans_class = transformation_record.get("transform_class") if isinstance(transformation_record, dict) else getattr(transformation_record, "transform_class", None)
        if hasattr(trans_class, "value"):
            trans_class = trans_class.value
        trans_confidence = transformation_record.get("confidence", 0.0) if isinstance(transformation_record, dict) else getattr(transformation_record, "confidence", 0.0)
        
        if not trans_class or trans_confidence < 0.7:
            return
        
        # Map transformation classes to effect classes
        trans_to_effect_map = {
            "configuration_cycle_step": "configuration_cycle_step",
            "rotation_or_permutation": "rotation_or_permutation",
            "spoke_endpoint_swap": "spoke_endpoint_swap",
            "link_rewire": "link_rewire",
            "hub_phase_change": "hub_phase_change",
            "goal_alignment_change": "goal_alignment_change",
        }
        
        new_effect_class = trans_to_effect_map.get(str(trans_class))
        if not new_effect_class:
            return
        
        # Update effect classes in claims that are reversible_movement or state_transition
        for claim in compiled_delta.claims:
            if getattr(claim, "kind", "") == "action_effect":
                old_effect = getattr(claim, "effect_class", "unknown")
                if old_effect in ("reversible_movement", "state_transition"):
                    # Upgrade to specific transformation-backed class
                    claim.effect_class = new_effect_class
                    if hasattr(claim, "props"):
                        claim.props["graph_transformation_class"] = new_effect_class
                        claim.props["graph_transformation_confidence"] = trans_confidence
