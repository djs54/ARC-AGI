# Plan for B150 — ARC Grid Diff Engine: Level Transition Analysis

## Card Metadata

- **Card ID**: B150
- **Priority**: P0
- **Dependencies**: B157

## Summary

Build a deterministic grid analysis engine that computes structured diffs between game states — both per-action (what did each action do?) and per-level (what changed from level start to level end?). This provides the evidence layer for cross-level learning. No LLM calls — pure algorithmic analysis.

### ARC-AGI-3 Interactive Game Model

ARC-AGI-3 is NOT classic ARC with static training examples. It's an interactive multi-level game:
- Games have 7-8 levels, progressing from simple to complex
- No training examples — the agent discovers patterns through play
- Early levels are implicit tutorials. Solved levels = training pairs.
- Grid: 64x64, colors 0-15. Actions: ACTION1-4 (directional), ACTION5 (interact), ACTION6 (coordinate), ACTION7 (undo)

This card provides two analysis modes:
- **Step-level**: `diff_frames()` compares grid before and after a single action → feeds ActionFact tracking
- **Level-level**: `diff_grids()` compares level start vs level end → feeds cross-level learning (B151)

## Technical Approach

### 1. Create `agents/arc3/grid_analysis.py`

#### Data structures

```python
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

@dataclass
class LevelPattern:
    """Cross-level consensus: what's common across solved levels."""
    consistent_action_effects: Dict[str, str]  # action_id -> observed effect description
    consistent_color_map: Dict[int, int]  # color transformations across levels
    consistent_spatial_pattern: Optional[str]  # "translation", "rotation", etc.
    game_rule_summary: str  # human-readable one-liner
    confidence: float  # 0-1 based on cross-level agreement
    n_levels: int
```

#### GridDiffEngine class

```python
class GridDiffEngine:
    def diff_grids(self, start_grid: List[List[int]], end_grid: List[List[int]]) -> GridDiff:
        """Compute structured diff between level start and level end grid."""
        # 1. Compare dimensions (should be same for same level)
        # 2. Cell-by-cell comparison
        # 3. Build CellChange list
        # 4. Detect color mapping: check if ALL instances of color A map to color B
        # 5. Find connected regions of changed cells (BFS flood fill)
        # 6. Check symmetry in the change pattern
        # 7. Compute fraction_changed

    def diff_frames(self, frame_before: List[List[int]], frame_after: List[List[int]], action_id: str) -> FrameDelta:
        """Compare grid before and after a single action."""
        # 1. Cell-by-cell diff
        # 2. Classify effect: no_change, single_cell_toggle, object_movement, complex
        # 3. If movement: compute direction vector from centroid shift
        # Returns FrameDelta for ActionFact tracking

    def extract_connected_components(self, grid: List[List[int]], color: int) -> List[ConnectedRegion]:
        """BFS flood fill for connected components of a specific color."""

    def detect_symmetry(self, grid: List[List[int]]) -> List[str]:
        """Check for horizontal, vertical, and rotational symmetry."""

    def detect_color_mapping(self, start_grid, end_grid) -> Optional[Dict[int, int]]:
        """Check if end_grid is a color-remapped version of start_grid."""

    def cross_level_consensus(self, level_diffs: List[GridDiff]) -> LevelPattern:
        """Find common patterns across all solved level diffs.

        1. Intersect color mappings across levels
        2. Check spatial pattern consistency
        3. Aggregate action effects from FrameDeltas
        4. Classify game rule from consensus
        5. Confidence = fraction of levels that agree
        """
```

### 2. Integration with orchestrator

Two integration points:

**Per-step (FrameDelta)**: After each action in the game loop:
```python
# In orchestrator, after receiving action result:
if hasattr(self, '_last_grid') and self._last_grid:
    from agents.arc3.grid_analysis import GridDiffEngine
    delta = GridDiffEngine().diff_frames(self._last_grid, current_grid, action_id)
    self._frame_deltas.append(delta)
    # Feed into ActionFact tracking
self._last_grid = current_grid
```

**Per-level (GridDiff)**: At level transitions (B157's `_on_level_transition()`):
```python
# Called by B157 when a level is won:
def _analyze_level_transition(self, solved_level):
    diff_engine = GridDiffEngine()
    level_diff = diff_engine.diff_grids(solved_level["start_grid"], solved_level["end_grid"])
    self._solved_level_diffs.append(level_diff)

    # Cross-level consensus across all solved levels
    if len(self._solved_level_diffs) >= 1:
        self._level_pattern = diff_engine.cross_level_consensus(self._solved_level_diffs)
```

### 3. Helper: grid_characteristic_summary

```python
def grid_characteristic_summary(grid: List[List[int]]) -> Dict[str, Any]:
    """Compute structural characteristics for memory keying (used by B155)."""
    rows = len(grid)
    cols = len(grid[0]) if grid else 0
    colors = set()
    for row in grid:
        for cell in row:
            colors.add(cell)
    return {
        "rows": rows, "cols": cols,
        "n_colors": len(colors), "colors": sorted(colors),
        "symmetry": GridDiffEngine().detect_symmetry(grid),
    }
```

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/grid_analysis.py` | NEW: GridDiffEngine, GridDiff, FrameDelta, LevelPattern, CellChange, ConnectedRegion, grid_characteristic_summary |
| `agents/arc3/orchestrator.py` | Call diff engine per-step (FrameDelta) and at level transitions (GridDiff); store `_solved_level_diffs`, `_level_pattern`, `_frame_deltas` |
| `tests/test_b150_grid_diff_engine.py` | NEW: comprehensive test suite |

## Test Plan

```python
# test_b150_grid_diff_engine.py

# 1. test_diff_identical_grids — same start/end, zero changes
# 2. test_diff_single_cell_change — one cell differs
# 3. test_diff_color_swap — systematic color substitution
# 4. test_diff_frames_movement — detect object movement after action
# 5. test_diff_frames_no_change — action had no effect
# 6. test_diff_frames_toggle — single cell toggle (ACTION5-like)
# 7. test_connected_components — verify BFS on 64x64 grid
# 8. test_detect_symmetry — horizontal/vertical symmetry
# 9. test_detect_color_mapping_consistent
# 10. test_cross_level_consensus_2_levels — 2 solved levels with same pattern
# 11. test_cross_level_consensus_disagreement — levels with different patterns
# 12. test_level_pattern_confidence — confidence increases with agreement
# 13. test_grid_characteristic_summary — produces correct summary
```

## Validation Commands

```bash
python3 -m pytest tests/test_b150_grid_diff_engine.py -v
python3 -m pytest tests/test_arc3_orchestrator.py -q
```

## Risks / Constraints

- **64x64 grids**: ARC-AGI-3 grids are 64x64 (4096 cells). Connected component extraction is O(n) — fine. Symmetry detection is O(n) — fine. No performance concern.
- **Per-step overhead**: `diff_frames()` runs after every action. On 64x64 grids this is ~4096 comparisons — negligible.
- **Cross-level consensus with 1 level**: With only 1 solved level, consensus is just the single diff. Confidence should be low (0.3-0.5). It becomes meaningful with 2+ levels.
- **Frame delta requires grid capture**: The orchestrator must capture the grid BEFORE and AFTER each action. B157 ensures `_level_start_grid` is captured. Per-step grid capture needs `_last_grid` tracking.

## Done When

- GridDiffEngine produces correct diffs for hand-crafted 64x64 test cases
- FrameDelta correctly identifies per-action effects (movement, toggle, no-change)
- Cross-level consensus identifies common patterns across 2+ solved levels
- LevelPattern confidence increases with cross-level agreement
- Orchestrator calls diff engine per-step and at level transitions
- All tests pass
