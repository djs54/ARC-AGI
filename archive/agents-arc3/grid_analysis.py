"""ARC Grid Diff Engine — Level Transition Analysis (B150)."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
import collections
import logging

logger = logging.getLogger(__name__)

@dataclass
class CellChange:
    row: int
    col: int
    from_color: int
    to_color: int

@dataclass
class ConnectedRegion:
    color: int
    cells: List[tuple[int, int]]  # (row, col)
    bounding_box: tuple[int, int, int, int]  # (min_row, min_col, max_row, max_col)
    size: int

@dataclass
class GridDiff:
    cells_changed: List[CellChange]
    color_mapping: Dict[int, int]  # from_color -> to_color (systematic only)
    size_changed: bool
    input_size: tuple[int, int]
    output_size: tuple[int, int]
    unchanged_mask: List[List[bool]]
    changed_regions: List[ConnectedRegion]
    symmetry_axes: List[str]
    fraction_changed: float  # 0-1

@dataclass
class FrameDelta:
    """Per-action analysis: what changed after one action."""
    action_id: str
    cells_changed: List[CellChange]
    n_cells_changed: int
    apparent_effect: str  # "moved_object", "toggled_cell", "no_change", "complex"
    direction: Optional[tuple[int, int]]  # (dr, dc) if movement detected
    new_colors_introduced: List[int] = field(default_factory=list)
    colors_removed: List[int] = field(default_factory=list)

@dataclass
class ObjectDelta:
    """A063: Structured change in a single color/component."""
    color: int
    size_delta: int
    centroid_delta: tuple[float, float]
    expansion: bool
    contraction: bool

@dataclass
class ObjectProgressSummary:
    """A063: Aggregate object-centric progress evidence."""
    score: float
    components: Dict[str, float]
    summary: str
    deltas: List[ObjectDelta]

@dataclass
class LevelPattern:
    """Cross-level consensus: what's common across solved levels."""
    consistent_action_effects: Dict[str, str]  # action_id -> observed effect description
    consistent_color_map: Dict[int, int]  # color transformations across levels
    consistent_spatial_pattern: Optional[str]  # "translation", "rotation", etc.
    game_rule_summary: str  # human-readable one-liner
    confidence: float  # 0-1 based on cross-level agreement
    n_levels: int

@dataclass
class PatternRegion:
    """B167: A bounded rectangular region containing a meaningful pattern."""
    bounding_box: tuple[int, int, int, int]  # (min_row, min_col, max_row, max_col)
    pattern: List[List[int]]  # Cropped sub-grid (colors within bounding box)
    center: tuple[float, float]  # (row, col) centroid
    color_palette: Set[int]  # Unique colors in region (excluding background)
    size: int  # Number of non-background cells
    location_hint: str  # "corner_bl", "corner_br", "center", "edge_top", etc.

@dataclass
class RegionComparison:
    """B167: Result of comparing two pattern regions."""
    similarity: Optional[float]  # 0.0 to 1.0; None if undefined (e.g. no foreground)
    exact_match: bool
    cells_matching: int
    cells_total: int
    color_shifted: bool = False  # A050: True if patterns match structurally with a color swap
    color_shift: Optional[Dict[int, int]] = None  # If patterns match with a color swap
    description: str = ""  # "exact match", "partial match (72%)", etc.

class GridDiffEngine:
    """Pure deterministic analysis of ARC grids."""

    def extract_pattern_regions(self, grid: List[List[int]], background_color: int = 0, min_size: int = 4, max_size_fraction: float = 0.40) -> List[PatternRegion]:
        """B167: Find distinct pattern regions in the grid."""
        if not grid: return []
        rows = len(grid)
        cols = len(grid[0])
        total_pixels = rows * cols
        
        components = self.extract_connected_components(grid, color=-1)
        pattern_regions = []
        
        for comp in components:
            if comp.size < min_size: continue
            if comp.size > total_pixels * max_size_fraction: continue
            
            min_r, min_c, max_r, max_c = comp.bounding_box
            pattern = self.crop_region(grid, comp.bounding_box)
            palette = {cell for row in pattern for cell in row if cell != background_color}
            
            # Determine location hint
            v_pos = "center"
            if min_r == 0: v_pos = "top"
            elif max_r == rows - 1: v_pos = "bottom"
            
            h_pos = ""
            if min_c == 0: h_pos = "left"
            elif max_c == cols - 1: h_pos = "right"
            
            if v_pos in ("top", "bottom") and h_pos:
                hint = f"corner_{v_pos[0]}{h_pos[0]}"
            elif v_pos != "center":
                hint = f"edge_{v_pos}"
            elif h_pos:
                hint = f"edge_{h_pos}"
            else:
                hint = "center"
                
            pattern_regions.append(PatternRegion(
                bounding_box=comp.bounding_box,
                pattern=pattern,
                center=((min_r + max_r) / 2.0, (min_c + max_c) / 2.0),
                color_palette=palette,
                size=comp.size,
                location_hint=hint
            ))
            
        return sorted(pattern_regions, key=lambda x: x.size, reverse=True)

    def crop_region(self, grid: List[List[int]], bounding_box: tuple[int, int, int, int]) -> List[List[int]]:
        """B167: Extract a rectangular sub-grid."""
        min_r, min_c, max_r, max_c = bounding_box
        rows = len(grid)
        cols = len(grid[0]) if rows > 0 else 0
        
        # Clamp to grid bounds
        min_r = max(0, min_r)
        max_r = min(rows - 1, max_r)
        min_c = max(0, min_c)
        max_c = min(cols - 1, max_c)
        
        return [row[min_c:max_c+1] for row in grid[min_r:max_r+1]]

    def compare_regions(self, region_a: PatternRegion, region_b: PatternRegion, allow_color_shift: bool = True, background_color: int = 0) -> RegionComparison:
        """B167: Compare two pattern regions for similarity.
        B168: Exclude background cells, handle small size differences via overlap.
        """
        pat_a = region_a.pattern
        pat_b = region_b.pattern

        rows_a = len(pat_a)
        cols_a = len(pat_a[0]) if rows_a > 0 else 0
        rows_b = len(pat_b)
        cols_b = len(pat_b[0]) if rows_b > 0 else 0

        # B168: Handle small size differences by comparing overlapping region
        if rows_a != rows_b or cols_a != cols_b:
            overlap_rows = min(rows_a, rows_b)
            overlap_cols = min(cols_a, cols_b)
            # Only tolerate small differences (within 2 cells)
            if abs(rows_a - rows_b) > 2 or abs(cols_a - cols_b) > 2:
                return RegionComparison(similarity=0.0, exact_match=False, cells_matching=0, cells_total=max(rows_a*cols_a, rows_b*cols_b), description="size mismatch")
            # Compare over the overlap region, penalize for size difference
            fg_total = 0
            fg_matching = 0
            for r in range(overlap_rows):
                for c in range(overlap_cols):
                    ca = pat_a[r][c]
                    cb = pat_b[r][c]
                    if ca != background_color or cb != background_color:
                        fg_total += 1
                        if ca == cb:
                            fg_matching += 1
            # Count non-overlap cells as non-matching foreground
            for r in range(overlap_rows, max(rows_a, rows_b)):
                src = pat_a if rows_a > rows_b else pat_b
                for c in range(len(src[r]) if r < len(src) else 0):
                    if src[r][c] != background_color:
                        fg_total += 1
            for r in range(min(rows_a, rows_b)):
                for c in range(overlap_cols, max(cols_a, cols_b)):
                    src = pat_a if cols_a > cols_b else pat_b
                    if c < len(src[r]):
                        if src[r][c] != background_color:
                            fg_total += 1
            similarity = fg_matching / fg_total if fg_total > 0 else 0.0
            return RegionComparison(
                similarity=similarity, exact_match=False,
                cells_matching=fg_matching, cells_total=fg_total,
                description=f"overlap match ({similarity:.1%})"
            )

        # B168: Exclude background cells from similarity calculation
        fg_total = 0
        fg_matching = 0
        all_total = rows_a * cols_a
        all_matching = 0
        mapping = {}
        inconsistent_shift = False

        for r in range(rows_a):
            for c in range(cols_a):
                ca = pat_a[r][c]
                cb = pat_b[r][c]

                if ca == cb:
                    all_matching += 1

                # Count foreground cells (either side non-background)
                if ca != background_color or cb != background_color:
                    fg_total += 1
                    if ca == cb:
                        fg_matching += 1

                if allow_color_shift and not inconsistent_shift:
                    if ca in mapping:
                        if mapping[ca] != cb:
                            inconsistent_shift = True
                    else:
                        mapping[ca] = cb

        # A050: Kill the "= 1.0" cliffs.
        if fg_total == 0:
            return RegionComparison(
                similarity=None,
                exact_match=False,
                cells_matching=0,
                cells_total=0,
                description="no foreground"
            )

        similarity = fg_matching / fg_total
        cells_total = fg_total
        cells_matching = fg_matching

        exact = (all_matching == all_total)
        color_shifted = False
        shift_result = None

        if not exact and allow_color_shift and not inconsistent_shift:
            # Consistent color mapping found (structural match)
            shift_result = mapping
            color_shifted = True
            desc = f"color-shifted partial match ({similarity:.1%})"
        elif exact:
            desc = "exact match"
        else:
            desc = f"partial match ({similarity:.1%})"

        return RegionComparison(
            similarity=similarity,
            exact_match=exact,
            # A050: Keep raw foreground match counts even for color-shifted matches.
            # Downstream callers rely on these counters for honest progress deltas.
            cells_matching=cells_matching,
            cells_total=cells_total,
            color_shifted=color_shifted,
            color_shift=shift_result,
            description=desc
        )

    def find_reference_goal_pair(self, regions: List[PatternRegion], rows: int, cols: int) -> Optional[tuple[PatternRegion, PatternRegion]]:
        """B167: Identify reference (static target) and goal (dynamic).

        Prefer pairs that are structurally similar, with the reference anchored in a
        corner (especially bottom-left) and the goal nearer the center / upper playfield.
        """
        if len(regions) < 2:
            return None

        best_pair: Optional[tuple[PatternRegion, PatternRegion]] = None
        best_score = float("-inf")

        def reference_bias(region: PatternRegion) -> float:
            if region.location_hint == "corner_bl":
                return 12.0
            if "corner" in region.location_hint:
                return 9.0
            if "edge" in region.location_hint:
                return 5.0
            return 1.5

        def goal_bias(region: PatternRegion, reference: PatternRegion) -> float:
            score = 0.0
            if region.location_hint == "center":
                score += 8.0
            elif "edge_top" in region.location_hint or region.center[0] <= rows * 0.45:
                score += 6.0
            elif "corner" not in region.location_hint:
                score += 4.0
            if "corner" in region.location_hint:
                score -= 3.0
            if region.location_hint == reference.location_hint:
                score -= 4.0
            return score

        for i in range(len(regions)):
            for j in range(i + 1, len(regions)):
                ra, rb = regions[i], regions[j]
                size_ratio = max(ra.size, rb.size) / max(min(ra.size, rb.size), 1)
                if size_ratio > 1.75:
                    continue

                structural = self.compare_regions(ra, rb, allow_color_shift=True).similarity
                structural = float(structural or 0.0)

                for reference, goal in ((ra, rb), (rb, ra)):
                    overlap = len(reference.color_palette & goal.color_palette)
                    palette_bonus = 2.0 if overlap > 0 else (1.5 if structural >= 0.95 else 0.0)
                    separation = abs(reference.center[0] - goal.center[0]) + abs(reference.center[1] - goal.center[1])
                    separation_bonus = min(separation / max(rows + cols, 1), 1.0) * 4.0
                    score = (
                        structural * 20.0
                        + reference_bias(reference)
                        + goal_bias(goal, reference)
                        + palette_bonus
                        + separation_bonus
                        - abs(1.0 - size_ratio) * 3.0
                    )
                    if score > best_score:
                        best_score = score
                        best_pair = (reference, goal)

        return best_pair

    def diff_grids(self, start_grid: List[List[int]], end_grid: List[List[int]]) -> GridDiff:
        """Compute structured diff between start and end grid."""
        in_rows = len(start_grid)
        in_cols = len(start_grid[0]) if in_rows > 0 else 0
        out_rows = len(end_grid)
        out_cols = len(end_grid[0]) if out_rows > 0 else 0
        
        size_changed = (in_rows != out_rows or in_cols != out_cols)
        
        # We compare up to the common dimensions
        max_rows = max(in_rows, out_rows)
        max_cols = max(in_cols, out_cols)
        
        cells_changed = []
        unchanged_mask = [[False for _ in range(max_cols)] for _ in range(max_rows)]
        
        for r in range(max_rows):
            for c in range(max_cols):
                in_val = start_grid[r][c] if r < in_rows and c < in_cols else -1
                out_val = end_grid[r][c] if r < out_rows and c < out_cols else -1
                
                if in_val != out_val:
                    cells_changed.append(CellChange(r, c, in_val, out_val))
                else:
                    if r < max_rows and c < max_cols:
                        unchanged_mask[r][c] = True
        
        # Systematic color mapping detection
        color_mapping = self.detect_color_mapping(start_grid, end_grid) or {}
        
        # Changed regions (connected components of changed cells)
        change_grid = [[-1 for _ in range(max_cols)] for _ in range(max_rows)]
        for change in cells_changed:
            change_grid[change.row][change.col] = change.to_color
            
        changed_regions = []
        visited = set()
        for change in cells_changed:
            if (change.row, change.col) not in visited:
                region_cells = self._flood_fill(change_grid, change.row, change.col, change.to_color, visited)
                if region_cells:
                    min_r = min(rc[0] for rc in region_cells)
                    max_r = max(rc[0] for rc in region_cells)
                    min_c = min(rc[1] for rc in region_cells)
                    max_c = max(rc[1] for rc in region_cells)
                    changed_regions.append(ConnectedRegion(
                        color=change.to_color,
                        cells=region_cells,
                        bounding_box=(min_r, min_c, max_r, max_c),
                        size=len(region_cells)
                    ))
        
        # Symmetry detection in output
        symmetry_axes = self.detect_symmetry(end_grid)
        
        total_cells = max_rows * max_cols
        fraction_changed = len(cells_changed) / total_cells if total_cells > 0 else 0
        
        return GridDiff(
            cells_changed=cells_changed,
            color_mapping=color_mapping,
            size_changed=size_changed,
            input_size=(in_rows, in_cols),
            output_size=(out_rows, out_cols),
            unchanged_mask=unchanged_mask,
            changed_regions=changed_regions,
            symmetry_axes=symmetry_axes,
            fraction_changed=fraction_changed
        )

    def diff_frames(self, frame_before: List[List[int]], frame_after: List[List[int]], action_id: str) -> FrameDelta:
        """Compare grid before and after a single action."""
        diff = self.diff_grids(frame_before, frame_after)
        
        n_changed = len(diff.cells_changed)
        effect = "no_change"
        direction = None
        
        if n_changed == 0:
            effect = "no_change"
        elif n_changed <= 2:
            effect = "toggled_cell"
        else:
            # Check for movement
            # Group cells by color before and after
            colors_before = self.extract_connected_components(frame_before)
            colors_after = self.extract_connected_components(frame_after)
            
            if len(colors_before) == len(colors_after):
                # Potential movement: compute centroid shift of the largest component
                # (Simple heuristic for now)
                if colors_before and colors_after:
                    c1 = colors_before[0]
                    c2 = colors_after[0]
                    dr = (sum(rc[0] for rc in c2.cells)/c2.size) - (sum(rc[0] for rc in c1.cells)/c1.size)
                    dc = (sum(rc[1] for rc in c2.cells)/c2.size) - (sum(rc[1] for rc in c1.cells)/c1.size)
                    if abs(dr) > 0.5 or abs(dc) > 0.5:
                        effect = "moved_object"
                        direction = (round(dr), round(dc))
                    else:
                        effect = "complex"
            else:
                effect = "complex"
                
        before_colors = {
            int(cell)
            for row in frame_before
            for cell in row
            if int(cell) != 0
        }
        after_colors = {
            int(cell)
            for row in frame_after
            for cell in row
            if int(cell) != 0
        }

        return FrameDelta(
            action_id=action_id,
            cells_changed=diff.cells_changed,
            n_cells_changed=n_changed,
            apparent_effect=effect,
            direction=direction,
            new_colors_introduced=sorted(after_colors - before_colors),
            colors_removed=sorted(before_colors - after_colors),
        )

    def calculate_coordinate_relevance(self, requested_coord: Tuple[int, int], changed_cells: List[CellChange]) -> Dict[str, Any]:
        """A062: Compare requested coordinate against observed cell changes."""
        if not changed_cells:
            return {"min_dist": None, "mean_dist": None, "n_cells": 0, "changed_coords": []}
            
        distances = []
        req_r, req_c = requested_coord
        
        for cell in changed_cells:
            dist = ((cell.row - req_r)**2 + (cell.col - req_c)**2)**0.5
            distances.append(dist)
            
        return {
            "min_dist": min(distances),
            "mean_dist": sum(distances) / len(distances),
            "n_cells": len(changed_cells),
            "changed_coords": [(c.row, c.col) for c in changed_cells]
        }

    def compute_object_progress(self, prev_grid: List[List[int]], next_grid: List[List[int]], roles: Dict[int, dict]) -> ObjectProgressSummary:
        """A063: Compute object-centric progress components."""
        prev_components = self.extract_connected_components(prev_grid, color=-1)
        next_components = self.extract_connected_components(next_grid, color=-1)
        
        # Group by color
        prev_by_color = collections.defaultdict(list)
        for c in prev_components: prev_by_color[c.color].append(c)
        
        next_by_color = collections.defaultdict(list)
        for c in next_components: next_by_color[c.color].append(c)
        
        deltas = []
        components = {}
        
        def role_for(color_value: int) -> Any:
            role_data = roles.get(color_value) or roles.get(str(color_value)) or {}
            role = role_data.get("role") if isinstance(role_data, dict) else None
            return role.value if hasattr(role, "value") else role

        # Analyze each color with role awareness
        for color in set(prev_by_color.keys()) | set(next_by_color.keys()):
            p_list = prev_by_color[color]
            n_list = next_by_color[color]
            role = role_for(color)
            total_size_delta = sum(c.size for c in n_list) - sum(c.size for c in p_list)

            if not (len(p_list) == 1 and len(n_list) == 1):
                if role == "player" and total_size_delta > 0:
                    components["player_expansion"] = components.get("player_expansion", 0.0) + min(0.5, 0.05 * total_size_delta)
                elif role == "path" and total_size_delta < 0:
                    components["path_consumption"] = components.get("path_consumption", 0.0) + min(0.5, 0.05 * abs(total_size_delta))
                elif role == "collectible" and total_size_delta < 0:
                    components["collectible_pickup"] = components.get("collectible_pickup", 0.0) + min(0.5, 0.1 * abs(total_size_delta))
            
            # Simple 1-to-1 matching by single-component colors
            if len(p_list) == 1 and len(n_list) == 1:
                p = p_list[0]
                n = n_list[0]
                size_delta = n.size - p.size
                
                pr1, pc1, pr2, pc2 = p.bounding_box
                nr1, nc1, nr2, nc2 = n.bounding_box
                
                p_cent = ((pr1 + pr2) / 2.0, (pc1 + pc2) / 2.0)
                n_cent = ((nr1 + nr2) / 2.0, (nc1 + nc2) / 2.0)
                dr = n_cent[0] - p_cent[0]
                dc = n_cent[1] - p_cent[1]
                
                if role == "player":
                    if size_delta > 0:
                        components["player_expansion"] = components.get("player_expansion", 0.0) + 0.2
                    if abs(dr) > 0.1 or abs(dc) > 0.1:
                        components["player_movement"] = components.get("player_movement", 0.0) + 0.1
                elif role == "path" and size_delta < 0:
                    components["path_consumption"] = components.get("path_consumption", 0.0) + 0.3
                elif role == "collectible" and size_delta < 0:
                    components["collectible_pickup"] = components.get("collectible_pickup", 0.0) + 0.5
                
                deltas.append(ObjectDelta(
                    color=color, size_delta=size_delta,
                    centroid_delta=(dr, dc), expansion=size_delta > 0, contraction=size_delta < 0
                ))

        # Goal approach
        def coerce_color_key(value: Any) -> Any:
            try:
                return int(value)
            except (TypeError, ValueError):
                return value

        player_colors = [coerce_color_key(c) for c, r in roles.items() if (r.get("role") == "player" or (hasattr(r.get("role"), "value") and r.get("role").value == "player"))]
        goal_colors = [coerce_color_key(c) for c, r in roles.items() if (r.get("role") == "goal" or (hasattr(r.get("role"), "value") and r.get("role").value == "goal"))]
        
        if player_colors and goal_colors:
            def min_dist(grid_components, c_list1, c_list2):
                min_d = float("inf")
                for c1 in [comp for comp in grid_components if comp.color in c_list1]:
                    for c2 in [comp for comp in grid_components if comp.color in c_list2]:
                        r1, c1_r, r2, c1_c = c1.bounding_box
                        r3, c3_r, r4, c3_c = c2.bounding_box
                        cent1 = ((r1+r2)/2.0, (c1_r+c1_c)/2.0)
                        cent2 = ((r3+r4)/2.0, (c3_r+c3_c)/2.0)
                        d = ((cent1[0]-cent2[0])**2 + (cent1[1]-cent2[1])**2)**0.5
                        if d < min_d: min_d = d
                return min_d
            
            d_prev = min_dist(prev_components, player_colors, goal_colors)
            d_next = min_dist(next_components, player_colors, goal_colors)
            
            if d_next < d_prev and d_prev != float("inf"):
                components["goal_approach"] = round((d_prev - d_next) * 0.1, 3)
                
        score = sum(components.values())
        summary_parts = [f"{k}:{v:.2f}" for k, v in components.items() if abs(v) > 0.01]
        summary = ", ".join(summary_parts) if summary_parts else "no structural progress"
        
        return ObjectProgressSummary(score=round(score, 3), components=components, summary=summary, deltas=deltas)

    def _flood_fill(self, grid: List[List[int]], r: int, c: int, color: int, visited: Set[tuple[int, int]]) -> List[tuple[int, int]]:
        rows = len(grid)
        cols = len(grid[0])
        q = collections.deque([(r, c)])
        visited.add((r, c))
        region = []
        
        while q:
            curr_r, curr_c = q.popleft()
            region.append((curr_r, curr_c))
            
            for dr, dc in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                nr, nc = curr_r + dr, curr_c + dc
                if 0 <= nr < rows and 0 <= nc < cols and (nr, nc) not in visited and grid[nr][nc] == color:
                    visited.add((nr, nc))
                    q.append((nr, nc))
        return region

    def extract_connected_components(self, grid: List[List[int]], color: int = -1, include_background: bool = False) -> List[ConnectedRegion]:
        """Find connected components. If color is -1, finds for all colors.
        If include_background is False (default), skips color 0.
        """
        rows = len(grid)
        cols = len(grid[0]) if rows > 0 else 0
        visited = set()
        regions = []
        
        for r in range(rows):
            for c in range(cols):
                val = grid[r][c]
                if (r, c) not in visited:
                    if color == -1:
                        if val != 0 or include_background:
                            region_cells = self._flood_fill(grid, r, c, val, visited)
                            min_r = min(rc[0] for rc in region_cells)
                            max_r = max(rc[0] for rc in region_cells)
                            min_c = min(rc[1] for rc in region_cells)
                            max_c = max(rc[1] for rc in region_cells)
                            regions.append(ConnectedRegion(val, region_cells, (min_r, min_c, max_r, max_c), len(region_cells)))
                    elif val == color:
                        region_cells = self._flood_fill(grid, r, c, val, visited)
                        min_r = min(rc[0] for rc in region_cells)
                        max_r = max(rc[0] for rc in region_cells)
                        min_c = min(rc[1] for rc in region_cells)
                        max_c = max(rc[1] for rc in region_cells)
                        regions.append(ConnectedRegion(val, region_cells, (min_r, min_c, max_r, max_c), len(region_cells)))
        return sorted(regions, key=lambda r: r.size, reverse=True)

    def detect_symmetry(self, grid: List[List[int]]) -> List[str]:
        """Check for various types of symmetry."""
        if not grid or not grid[0]: return []
        rows = len(grid)
        cols = len(grid[0])
        axes = []
        
        # Horizontal (flip over horizontal axis)
        is_horiz = True
        for r in range(rows // 2):
            if grid[r] != grid[rows - 1 - r]:
                is_horiz = False
                break
        if is_horiz: axes.append("horizontal")
        
        # Vertical (flip over vertical axis)
        is_vert = True
        for r in range(rows):
            for c in range(cols // 2):
                if grid[r][c] != grid[r][cols - 1 - c]:
                    is_vert = False
                    break
            if not is_vert: break
        if is_vert: axes.append("vertical")
        
        # Diagonals (only if square)
        if rows == cols:
            is_diag_main = True
            for r in range(rows):
                for c in range(r + 1, cols):
                    if grid[r][c] != grid[c][r]:
                        is_diag_main = False
                        break
                if not is_diag_main: break
            if is_diag_main: axes.append("diagonal_main")
            
            is_diag_anti = True
            for r in range(rows):
                for c in range(cols - r - 1):
                    if grid[r][c] != grid[cols - 1 - c][rows - 1 - r]:
                        is_diag_anti = False
                        break
                if not is_diag_anti: break
            if is_diag_anti: axes.append("diagonal_anti")
            
        is_rot180 = True
        for r in range(rows):
            for c in range(cols):
                if grid[r][c] != grid[rows - 1 - r][cols - 1 - c]:
                    is_rot180 = False
                    break
            if not is_rot180: break
        if is_rot180: axes.append("rot180")
        
        return axes

    def detect_color_mapping(self, input_grid: List[List[int]], output_grid: List[List[int]]) -> Optional[Dict[int, int]]:
        """Check if output is a color-remapped version of input."""
        in_rows = len(input_grid)
        in_cols = len(input_grid[0]) if in_rows > 0 else 0
        out_rows = len(output_grid)
        out_cols = len(output_grid[0]) if out_rows > 0 else 0
        
        if in_rows != out_rows or in_cols != out_cols:
            return None
            
        mapping = {}
        for r in range(in_rows):
            for c in range(in_cols):
                in_val = input_grid[r][c]
                out_val = output_grid[r][c]
                if in_val in mapping:
                    if mapping[in_val] != out_val:
                        return None # Inconsistent mapping
                else:
                    mapping[in_val] = out_val
        
        is_identity = True
        for k, v in mapping.items():
            if k != v:
                is_identity = False
                break
        if is_identity: return None
        
        return mapping

    def cross_level_consensus(self, level_diffs: List[GridDiff]) -> LevelPattern:
        """Find the common transformation across all solved levels."""
        if not level_diffs:
            return LevelPattern({}, {}, None, "No levels solved", 0.0, 0)
            
        # 1. Consistent color map
        common_map = dict(level_diffs[0].color_mapping)
        for d in level_diffs[1:]:
            new_map = {}
            for k, v in d.color_mapping.items():
                if k in common_map and common_map[k] == v:
                    new_map[k] = v
            common_map = new_map
            
        # 2. Pattern classification
        pattern = "unknown"
        patterns = collections.Counter()
        for d in level_diffs:
            if d.color_mapping: patterns["recolor"] += 1
            if d.size_changed: patterns["resize"] += 1
            if d.symmetry_axes: patterns["symmetry"] += 1
            
        confidence_score = 0.0
        if patterns:
            top_pattern, count = patterns.most_common(1)[0]
            pattern = top_pattern
            
            # Base confidence from pattern agreement
            confidence_score = count / len(level_diffs)
            
            # Penalty for inconsistent details if pattern is recolor
            if pattern == "recolor":
                total_mappings = sum(len(d.color_mapping) for d in level_diffs)
                if total_mappings > 0:
                    consistency_ratio = (len(common_map) * len(level_diffs)) / total_mappings
                    confidence_score *= consistency_ratio
            
        summary = f"Consistent {pattern} pattern across {len(level_diffs)} levels"
        
        return LevelPattern(
            consistent_action_effects={}, # Integrated in orchestrator
            consistent_color_map=common_map,
            consistent_spatial_pattern=pattern,
            game_rule_summary=summary,
            confidence=round(confidence_score, 3),
            n_levels=len(level_diffs)
        )

def grid_characteristic_summary(grid: List[List[int]]) -> Dict[str, Any]:
    """Compute structural characteristics for memory keying and bootstrap reasoning."""
    rows = len(grid)
    cols = len(grid[0]) if grid else 0
    colors = set()
    for row in grid:
        for cell in row:
            colors.add(int(cell))

    engine = GridDiffEngine()
    symmetry = engine.detect_symmetry(grid)
    regions = engine.extract_connected_components(grid) if grid else []

    region_summaries: List[Dict[str, Any]] = []
    for region in regions[:8]:
        min_row, min_col, max_row, max_col = region.bounding_box
        region_summaries.append(
            {
                "color": int(region.color),
                "size": int(region.size),
                "center": {
                    "row": round((min_row + max_row) / 2.0, 2),
                    "col": round((min_col + max_col) / 2.0, 2),
                },
                "bounding_box": {
                    "min_row": int(min_row),
                    "min_col": int(min_col),
                    "max_row": int(max_row),
                    "max_col": int(max_col),
                },
            }
        )

    region_bits = []
    for item in region_summaries[:4]:
        center = item["center"]
        region_bits.append(
            f"color {item['color']} size {item['size']} near row {center['row']:.0f}, col {center['col']:.0f}"
        )

    text_summary = (
        f"Grid is {rows}x{cols} with {len(colors)} colors and {len(regions)} distinct non-background regions. "
        f"Symmetry: {', '.join(symmetry) if symmetry else 'none'}. "
        f"Largest regions: {'; '.join(region_bits) if region_bits else 'none detected'}."
    )

    return {
        "rows": rows,
        "cols": cols,
        "n_colors": len(colors),
        "colors": sorted(list(colors)),
        "distinct_colors": sorted(list(colors)),
        "symmetry": symmetry,
        "n_regions": len(regions),
        "region_sizes": [int(region.size) for region in regions],
        "regions": region_summaries,
        "text_summary": text_summary,
    }
