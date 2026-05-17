"""A110 - Click outcome evaluation telemetry.

Tracks and reports click action outcomes for debugging and evaluation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ClickOutcomeTelemetry:
    """Telemetry for a single click action."""
    
    step: int
    action_identity: str
    coordinate_required: bool
    missing_coordinate_click: bool
    click_candidate_id: Optional[str] = None
    click_candidate_role: Optional[str] = None
    click_candidate_rank: int = -1
    clicked_x: Optional[int] = None
    clicked_y: Optional[int] = None
    clicked_color: Optional[int] = None
    clicked_panel_id: Optional[str] = None
    frame_hash_before: Optional[str] = None
    frame_hash_after: Optional[str] = None
    config_hash_before: Optional[str] = None
    config_hash_after: Optional[str] = None
    click_supported: bool = False
    click_falsified: bool = False
    frame_delta: bool = False
    config_delta: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSONL serialization."""
        return {
            "step": self.step,
            "action_identity": self.action_identity,
            "coordinate_required": self.coordinate_required,
            "missing_coordinate_click": self.missing_coordinate_click,
            "click_candidate_id": self.click_candidate_id,
            "click_candidate_role": self.click_candidate_role,
            "click_candidate_rank": self.click_candidate_rank,
            "clicked_x": self.clicked_x,
            "clicked_y": self.clicked_y,
            "clicked_color": self.clicked_color,
            "clicked_panel_id": self.clicked_panel_id,
            "frame_delta": self.frame_delta,
            "config_delta": self.config_delta,
            "click_supported": self.click_supported,
            "click_falsified": self.click_falsified,
        }


class ClickTelemetryStore:
    """Store and aggregate click telemetry across a game."""
    
    def __init__(self):
        """Initialize telemetry store."""
        self.click_outcomes: List[ClickOutcomeTelemetry] = []
        self.candidate_tried: set[str] = set()
        self.candidate_supported: set[str] = set()
        self.candidate_falsified: set[str] = set()
    
    def record_click_outcome(self, telemetry: ClickOutcomeTelemetry) -> None:
        """Record a click outcome."""
        self.click_outcomes.append(telemetry)
        
        # Track candidate state
        if telemetry.click_candidate_id:
            action_identity = telemetry.action_identity
            self.candidate_tried.add(action_identity)
            
            if telemetry.click_supported:
                self.candidate_supported.add(action_identity)
            
            if telemetry.click_falsified:
                self.candidate_falsified.add(action_identity)
    
    def get_click_summary(self) -> Dict[str, Any]:
        """Get summary of click outcomes."""
        # Count missing coordinate clicks
        missing_coord_count = sum(
            1 for t in self.click_outcomes
            if t.missing_coordinate_click
        )
        
        # Count unique action identities
        unique_identities = set(t.action_identity for t in self.click_outcomes)
        
        # Check for null-click loop (many clicks with same identity and no delta)
        null_click_loop = (
            missing_coord_count > 0 or
            (len(unique_identities) == 1 and len(self.click_outcomes) > 5)
        )
        
        return {
            "click_count": len(self.click_outcomes),
            "missing_coordinate_click_count": missing_coord_count,
            "unique_action_identity_count": len(unique_identities),
            "click_candidates_tried": len(self.candidate_tried),
            "click_candidates_supported": len(self.candidate_supported),
            "click_candidates_falsified": len(self.candidate_falsified),
            "null_click_loop_detected": null_click_loop,
            "outcomes": [t.to_dict() for t in self.click_outcomes[-5:]],  # Last 5
        }
    
    def get_failure_message(self) -> Optional[str]:
        """Get human-readable failure message if clicks failed."""
        missing_coord_count = sum(
            1 for t in self.click_outcomes
            if t.missing_coordinate_click
        )
        
        if missing_coord_count > 3:
            return f"Click planning failed: {missing_coord_count} null ACTION6 clicks with no frame/configuration delta."
        
        # Check for repeated same coordinate with no progress
        if len(self.click_outcomes) > 5:
            # Get all action identities
            identities = [t.action_identity for t in self.click_outcomes]
            if len(set(identities)) == 1 and not any(t.click_supported for t in self.click_outcomes):
                identity = identities[0]
                return f"Click planning failed: {len(self.click_outcomes)} repeated {identity} clicks with no effect."
        
        return None
    
    def get_click_telemetry_row(self, telemetry: ClickOutcomeTelemetry) -> Dict[str, Any]:
        """Convert telemetry to JSONL row format."""
        row = telemetry.to_dict()
        row["click_summary"] = {
            "click_count": len(self.click_outcomes),
            "unique_identities": len(set(t.action_identity for t in self.click_outcomes)),
            "supported_count": len(self.candidate_supported),
            "falsified_count": len(self.candidate_falsified),
        }
        return row


def extract_click_telemetry_from_step(
    step_record: Dict[str, Any],
    frame_hash_before: Optional[str] = None,
    frame_hash_after: Optional[str] = None,
    config_hash_before: Optional[str] = None,
    config_hash_after: Optional[str] = None,
) -> Optional[ClickOutcomeTelemetry]:
    """Extract click telemetry from a step record.
    
    Args:
        step_record: The step record dict
        frame_hash_before: Frame hash before action
        frame_hash_after: Frame hash after action
        config_hash_before: Config hash before action
        config_hash_after: Config hash after action
    
    Returns:
        ClickOutcomeTelemetry if this was a click action, None otherwise.
    """
    action_id = step_record.get("action_id")
    
    # Only track click actions
    if action_id != "ACTION6":
        return None
    
    step = step_record.get("step", -1)
    action_identity = step_record.get("action_identity", f"ACTION6")
    x = step_record.get("x")
    y = step_record.get("y")
    
    # Determine if this was a missing coordinate click
    coordinate_required = step_record.get("coordinate_required", False)
    missing_coord = step_record.get("missing_coordinate_click", False)
    
    # Get candidate info
    candidate_id = step_record.get("click_candidate_id")
    candidate_role = step_record.get("click_candidate_role")
    candidate_rank = step_record.get("click_candidate_rank", -1)
    
    # Get outcome
    frame_delta = frame_hash_before != frame_hash_after if frame_hash_before else False
    config_delta = config_hash_before != config_hash_after if config_hash_before else False
    
    return ClickOutcomeTelemetry(
        step=step,
        action_identity=action_identity,
        coordinate_required=coordinate_required,
        missing_coordinate_click=missing_coord,
        click_candidate_id=candidate_id,
        click_candidate_role=candidate_role,
        click_candidate_rank=candidate_rank,
        clicked_x=x,
        clicked_y=y,
        clicked_color=step_record.get("clicked_color"),
        clicked_panel_id=step_record.get("clicked_panel_id"),
        frame_hash_before=frame_hash_before,
        frame_hash_after=frame_hash_after,
        config_hash_before=config_hash_before,
        config_hash_after=config_hash_after,
        frame_delta=frame_delta,
        config_delta=config_delta,
        click_supported=frame_delta or config_delta,
        click_falsified=not (frame_delta or config_delta),
    )
