from agents.arc3.hypothesis import StateNode
from agents.common.grid_hash import hash_grid


def test_hash_grid_matches_statenode() -> None:
    for grid in ([], [[0]], [[1, 2], [3, 4]], [[15] * 64] * 64):
        assert hash_grid(grid) == StateNode.hash_grid(grid)
