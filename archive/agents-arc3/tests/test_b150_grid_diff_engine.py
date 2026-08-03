"""Tests for B150 ARC Grid Diff Engine."""

import pytest
from agents.arc3.grid_analysis import GridDiffEngine, GridDiff, FrameDelta, LevelPattern, grid_characteristic_summary

@pytest.fixture
def engine():
    return GridDiffEngine()

def test_diff_identical_grids(engine):
    """Verify zero changes detected for identical grids."""
    grid = [[1, 2], [3, 4]]
    diff = engine.diff_grids(grid, grid)
    assert len(diff.cells_changed) == 0
    assert diff.size_changed is False
    assert diff.fraction_changed == 0.0

def test_diff_single_cell_change(engine):
    """Verify one cell change is correctly identified."""
    grid1 = [[1, 2], [3, 4]]
    grid2 = [[1, 2], [3, 5]]
    diff = engine.diff_grids(grid1, grid2)
    assert len(diff.cells_changed) == 1
    change = diff.cells_changed[0]
    assert change.row == 1
    assert change.col == 1
    assert change.from_color == 4
    assert change.to_color == 5

def test_diff_color_swap(engine):
    """Verify systematic color substitution detection."""
    # All 1s become 2s, all 2s become 1s
    grid1 = [[1, 2], [2, 1]]
    grid2 = [[2, 1], [1, 2]]
    diff = engine.diff_grids(grid1, grid2)
    assert diff.color_mapping == {1: 2, 2: 1}
    assert len(diff.cells_changed) == 4

def test_diff_frames_movement(engine):
    """Verify object movement detection."""
    # Move 2x2 square down by 1
    grid1 = [
        [1, 1, 0, 0],
        [1, 1, 0, 0],
        [0, 0, 0, 0],
        [0, 0, 0, 0]
    ]
    grid2 = [
        [0, 0, 0, 0],
        [1, 1, 0, 0],
        [1, 1, 0, 0],
        [0, 0, 0, 0]
    ]
    delta = engine.diff_frames(grid1, grid2, "ACTION2")
    assert delta.apparent_effect == "moved_object"
    assert delta.direction == (1, 0) # (dr, dc)

def test_diff_frames_no_change(engine):
    """Verify no-change detection."""
    grid = [[1, 1], [1, 1]]
    delta = engine.diff_frames(grid, grid, "ACTION1")
    assert delta.apparent_effect == "no_change"
    assert delta.n_cells_changed == 0

def test_diff_frames_toggle(engine):
    """Verify single cell toggle detection."""
    grid1 = [[0, 0], [0, 0]]
    grid2 = [[0, 1], [0, 0]]
    delta = engine.diff_frames(grid1, grid2, "ACTION5")
    assert delta.apparent_effect == "toggled_cell"
    assert delta.n_cells_changed == 1

def test_connected_components(engine):
    """Verify BFS correctly identifies separate regions."""
    grid = [
        [1, 1, 0, 2],
        [1, 1, 0, 2],
        [0, 0, 0, 0],
        [3, 3, 3, 3]
    ]
    # Color 1: 2x2 square (size 4)
    # Color 3: 4x1 horizontal line (size 4)
    # Color 2: 2x1 vertical line (size 2)
    regions = engine.extract_connected_components(grid)
    assert len(regions) == 3
    
    # Check largest regions (size 4)
    r1 = regions[0]
    assert r1.size == 4
    
    # Check color 2 region
    r2 = next(r for r in regions if r.color == 2)
    assert r2.size == 2
    assert r2.bounding_box == (0, 3, 1, 3)

def test_detect_symmetry(engine):
    """Test horizontal/vertical symmetry detection."""
    grid_h = [
        [1, 2, 1],
        [0, 3, 0],
        [1, 2, 1]
    ]
    axes = engine.detect_symmetry(grid_h)
    assert "horizontal" in axes
    
    grid_v = [
        [1, 0, 1],
        [2, 3, 2],
        [1, 0, 1]
    ]
    axes = engine.detect_symmetry(grid_v)
    assert "vertical" in axes

def test_detect_color_mapping_consistent(engine):
    """Test consistent color remapping."""
    grid1 = [[1, 2], [1, 2]]
    grid2 = [[3, 4], [3, 4]]
    mapping = engine.detect_color_mapping(grid1, grid2)
    assert mapping == {1: 3, 2: 4}

def test_cross_level_consensus_2_levels(engine):
    """Test consensus across 2 levels with same pattern."""
    diff1 = engine.diff_grids([[1]], [[2]]) # 1 -> 2
    diff2 = engine.diff_grids([[1, 1]], [[2, 2]]) # 1 -> 2
    
    pattern = engine.cross_level_consensus([diff1, diff2])
    assert pattern.consistent_color_map == {1: 2}
    assert pattern.confidence == 1.0
    assert pattern.n_levels == 2

def test_cross_level_consensus_disagreement(engine):
    """Test consensus across examples with different transformations."""
    # Level 1: 1 -> 2
    # Level 2: 1 -> 3
    diff1 = engine.diff_grids([[1]], [[2]])
    diff2 = engine.diff_grids([[1]], [[3]])
    
    pattern = engine.cross_level_consensus([diff1, diff2])
    assert pattern.consistent_color_map == {}
    assert pattern.confidence <= 0.5

def test_grid_characteristic_summary():
    """Verify grid characteristic summary output."""
    grid = [
        [1, 1, 1],
        [1, 0, 1],
        [1, 1, 1]
    ]
    chars = grid_characteristic_summary(grid)
    assert chars["rows"] == 3
    assert chars["cols"] == 3
    assert chars["n_colors"] == 2
    assert "horizontal" in chars["symmetry"]
    assert "vertical" in chars["symmetry"]
