
import pytest
import math
from agents.arc3.scene_graph import (
    build_scene_graph, 
    wl_canonical_hash, 
    wl_histogram_vector, 
    approximate_ged, 
    normalized_ged
)

def test_build_scene_graph_empty():
    sg = build_scene_graph([])
    assert len(sg.nodes) == 0
    assert len(sg.edges) == 0

def test_build_scene_graph_single_region():
    grid = [
        [0, 0, 0],
        [0, 1, 0],
        [0, 0, 0]
    ]
    sg = build_scene_graph(grid)
    # Depending on background handling, we might have 1 node (foreground) 
    # or multiple nodes if background is also extracted.
    # build_scene_graph calls extract_connected_components(include_background=True)
    assert len(sg.nodes) >= 1
    
    # Check if foreground is present
    fg_nodes = [n for n in sg.nodes.values() if n.color != 0]
    assert len(fg_nodes) == 1
    assert fg_nodes[0].color == 1
    assert fg_nodes[0].area == 1

def test_wl_canonical_hash_determinism():
    grid = [
        [1, 1, 0],
        [1, 0, 2],
        [0, 2, 2]
    ]
    sg1 = build_scene_graph(grid)
    sg2 = build_scene_graph(grid)
    
    h1 = wl_canonical_hash(sg1)
    h2 = wl_canonical_hash(sg2)
    assert h1 == h2
    assert h1 != "empty"

def test_wl_histogram_vector_self_similarity():
    grid = [
        [1, 1, 0],
        [1, 0, 2],
        [0, 2, 2]
    ]
    sg = build_scene_graph(grid)
    vec = wl_histogram_vector(sg)
    
    # Cosine similarity helper
    def cosine_sim(v1, v2):
        keys = set(v1.keys()) | set(v2.keys())
        dot = sum(v1.get(k, 0) * v2.get(k, 0) for k in keys)
        mag1 = math.sqrt(sum(v*v for v in v1.values()))
        mag2 = math.sqrt(sum(v*v for v in v2.values()))
        if mag1 == 0 or mag2 == 0: return 0.0
        return dot / (mag1 * mag2)
        
    assert cosine_sim(vec, vec) == pytest.approx(1.0)

def test_approximate_ged_monotonicity():
    """Verify GED increases as we add more components."""
    # g0 = [[0, 0, 0, 0, 0]] (empty foreground)
    g1 = [[1, 0, 0, 0, 0]]
    g2 = [[1, 0, 2, 0, 0]]
    g3 = [[1, 0, 2, 0, 3]]
    
    sg1 = build_scene_graph(g1)
    sg2 = build_scene_graph(g2)
    sg3 = build_scene_graph(g3)
    
    d12 = approximate_ged(sg1, sg2)
    d13 = approximate_ged(sg1, sg3)
    
    assert d12 < d13
    
    n12 = normalized_ged(sg1, sg2)
    n13 = normalized_ged(sg1, sg3)
    
    assert 0.0 < n12 < n13 <= 1.0
