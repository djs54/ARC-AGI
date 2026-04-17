"""Normalized ARC-AGI-3 observation/action schemas for SideQuests."""

from __future__ import annotations

from typing import Any, List, Mapping, Tuple, TypedDict


class ARC3ColorSummary(TypedDict):
    """Summary of a color's prevalence in a frame."""

    value: int
    count: int


class ARC3ShapeSummary(TypedDict):
    """Compact descriptor for a contiguous shape region."""

    color: int
    size: int
    coords: List[Tuple[int, int]]


class ARC3Observation(TypedDict):
    """Normalized snapshot of an ARC frame."""

    dataset_id: str
    task_id: str
    episode_num: int
    step_num: int
    grid: List[List[int]]
    colors: List[ARC3ColorSummary]
    shapes: List[ARC3ShapeSummary]
    available_actions: List[str]
    state: str
    energy_estimate: float
    frame_hash: str              # B88: SHA-256[:16] of grid
    invariant_regions: List[Any] # B88: discovered static regions


class ARC3Action(TypedDict):
    """Deterministic representation of an ARC command."""

    action_type: str
    grid_change: Mapping[str, Any]
    rationale: str
    deterministic_id: str
    metadata: Mapping[str, Any]
