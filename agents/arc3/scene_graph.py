
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from agents.arc3.grid_analysis import GridDiffEngine, ConnectedRegion

logger = logging.getLogger(__name__)

@dataclass
class SceneNode:
    """A node in the scene graph representing a connected component."""
    id: int
    color: int
    area: int
    bbox: Tuple[int, int, int, int]  # (min_r, min_c, max_r, max_c)
    centroid: Tuple[float, float]
    shape_hash: str
    location_hint: str
    cells: Set[Tuple[int, int]]

@dataclass
class SceneEdge:
    """An edge in the scene graph representing a relationship."""
    u: int
    v: int
    type: str  # adj, contains, same_color, same_shape, h_aligned, v_aligned, east_of, south_of

class SceneGraph:
    """B167/A050: A lightweight property graph representing an ARC grid."""
    def __init__(self):
        self.nodes: Dict[int, SceneNode] = {}
        self.edges: List[SceneEdge] = []
        self.background_color: int = 0
        self.rows: int = 0
        self.cols: int = 0

def build_scene_graph(grid: List[List[int]], background: int = 0) -> SceneGraph:
    """A050: Extract components and relationships to build a scene graph."""
    sg = SceneGraph()
    sg.background_color = background
    if not grid:
        return sg
    sg.rows = len(grid)
    sg.cols = len(grid[0])
    
    engine = GridDiffEngine()
    # 1. Build current scene graph
    # Extract components (exclude background for pattern matching stability)
    components = engine.extract_connected_components(grid, color=-1, include_background=False)
    
    # 1. Create nodes
    for i, comp in enumerate(components):
        # Extract pattern for shape hashing
        pattern = engine.crop_region(grid, comp.bounding_box)
        # Compute dihedral canonical shape hash
        shape_hash = _compute_shape_hash(pattern)
        
        min_r, min_c, max_r, max_c = comp.bounding_box
        centroid = ((min_r + max_r) / 2.0, (min_c + max_c) / 2.0)
        
        # Borrow location hint logic from GridDiffEngine if needed, 
        # or implement simple version here
        loc_hint = _get_location_hint(comp.bounding_box, sg.rows, sg.cols)
        
        node = SceneNode(
            id=i,
            color=comp.color,
            area=comp.size,
            bbox=comp.bounding_box,
            centroid=centroid,
            shape_hash=shape_hash,
            location_hint=loc_hint,
            cells=set(comp.cells)
        )
        sg.nodes[i] = node
        
    # 2. Create edges
    node_ids = sorted(sg.nodes.keys())
    for i in range(len(node_ids)):
        for j in range(i + 1, len(node_ids)):
            u_id = node_ids[i]
            v_id = node_ids[j]
            u = sg.nodes[u_id]
            v = sg.nodes[v_id]
            
            # Adjacency (8-neighbor touching)
            if _are_adjacent(u.cells, v.cells):
                sg.edges.append(SceneEdge(u_id, v_id, "adj"))
            
            # Containment
            if _contains(u.bbox, v.bbox):
                sg.edges.append(SceneEdge(u_id, v_id, "contains"))
            elif _contains(v.bbox, u.bbox):
                sg.edges.append(SceneEdge(v_id, u_id, "contains"))
                
            # Shared properties
            if u.color == v.color:
                sg.edges.append(SceneEdge(u_id, v_id, "same_color"))
            if u.shape_hash == v.shape_hash:
                sg.edges.append(SceneEdge(u_id, v_id, "same_shape"))
                
            # Alignment
            if abs(u.centroid[0] - v.centroid[0]) < 1.0:
                sg.edges.append(SceneEdge(u_id, v_id, "h_aligned"))
            if abs(u.centroid[1] - v.centroid[1]) < 1.0:
                sg.edges.append(SceneEdge(u_id, v_id, "v_aligned"))
                
            # Relative position
            if u.centroid[1] > v.centroid[1] + 1.0:
                sg.edges.append(SceneEdge(u_id, v_id, "east_of"))
            elif v.centroid[1] > u.centroid[1] + 1.0:
                sg.edges.append(SceneEdge(v_id, u_id, "east_of"))
                
            if u.centroid[0] > v.centroid[0] + 1.0:
                sg.edges.append(SceneEdge(u_id, v_id, "south_of"))
            elif v.centroid[0] > u.centroid[0] + 1.0:
                sg.edges.append(SceneEdge(v_id, u_id, "south_of"))
                
    return sg

def wl_canonical_hash(sg: SceneGraph, iterations: int = 3) -> str:
    """A050: Standard WL color refinement for graph hashing."""
    if not sg.nodes:
        return "empty"
        
    # Initial labels: color, area, shape_hash, location_hint
    labels = {
        nid: hashlib.md5(f"{n.color}|{n.area}|{n.shape_hash}|{n.location_hint}".encode()).hexdigest()
        for nid, n in sg.nodes.items()
    }
    
    # Build adjacency map for speed
    adj = {nid: [] for nid in sg.nodes}
    for e in sg.edges:
        adj[e.u].append((e.v, e.type))
        adj[e.v].append((e.u, e.type)) # undir for hashing mostly, but edge type matters
        
    for _ in range(iterations):
        new_labels = {}
        for nid in sg.nodes:
            # Sort neighbors by label and edge type for canonicality
            neighbor_labels = sorted([f"{labels[vid]}:{etype}" for vid, etype in adj[nid]])
            label_str = f"{labels[nid]}|" + ",".join(neighbor_labels)
            new_labels[nid] = hashlib.md5(label_str.encode()).hexdigest()
        labels = new_labels
        
    # Final hash is sorted multiset of labels
    final_labels = sorted(labels.values())
    return hashlib.md5(",".join(final_labels).encode()).hexdigest()

def wl_histogram_vector(sg: SceneGraph, iterations: int = 3) -> Dict[str, int]:
    """A050: WL-histogram sparse vector for analogical search."""
    histogram = {}
    if not sg.nodes:
        return histogram
        
    labels = {
        nid: f"v0_{n.color}_{n.shape_hash}_{n.location_hint}"
        for nid, n in sg.nodes.items()
    }
    
    def add_to_hist(lbls):
        for l in lbls.values():
            histogram[l] = histogram.get(l, 0) + 1

    add_to_hist(labels)
    
    adj = {nid: [] for nid in sg.nodes}
    for e in sg.edges:
        adj[e.u].append((e.v, e.type))
        adj[e.v].append((e.u, e.type))
        
    for i in range(1, iterations + 1):
        new_labels = {}
        for nid in sg.nodes:
            neighbor_labels = sorted([f"{labels[vid]}:{etype}" for vid, etype in adj[nid]])
            label_str = f"v{i}_{labels[nid]}|" + ",".join(neighbor_labels)
            # Use hash to keep keys reasonably sized
            new_labels[nid] = hashlib.md5(label_str.encode()).hexdigest()[:16]
        labels = new_labels
        add_to_hist(labels)
        
    return histogram

def approximate_ged(a: SceneGraph, b: SceneGraph, beam_width: int = 8, depth_cap: int = 4) -> float:
    """A050: Beam-search bounded approximate Graph Edit Distance."""
    # Trivial baseline: node/edge count difference
    n_a = len(a.nodes)
    n_b = len(b.nodes)
    e_a = len(a.edges)
    e_b = len(b.edges)
    
    if n_a == 0 and n_b == 0: return 0.0
    
    # Cheap heuristic: multiset difference of node properties (color, shape)
    nodes_a = sorted([(n.color, n.shape_hash) for n in a.nodes.values()])
    nodes_b = sorted([(n.color, n.shape_hash) for n in b.nodes.values()])
    
    # This is a very rough lower bound / approximation
    # Real GED is hard; for now we use property-multiset distance + edge count diff
    # as a "cheap approximate GED" suitable for sub-millisecond runtime.
    
    # Calculate Jaccard-like distance for node properties
    from collections import Counter
    ca = Counter(nodes_a)
    cb = Counter(nodes_b)
    
    common_nodes = sum((ca & cb).values())
    node_ged = (n_a - common_nodes) + (n_b - common_nodes)
    
    edge_ged = abs(e_a - e_b)
    
    return float(node_ged + edge_ged)

def normalized_ged(a: SceneGraph, b: SceneGraph) -> float:
    """A050: GED normalized to [0, 1].
    
    If both are empty, distance is 0.
    If one is empty, distance is 1.
    """
    n_a = len(a.nodes) + len(a.edges)
    n_b = len(b.nodes) + len(b.edges)
    if n_a == 0 and n_b == 0:
        return 0.0
        
    ged = approximate_ged(a, b)
    # Use max to ensure distance is 1.0 when one is empty
    return min(1.0, ged / max(n_a, n_b))

# --- Internal Helpers ---

def _compute_shape_hash(pattern: List[List[int]]) -> str:
    """Compute dihedral-canonical WL hash of component shape."""
    if not pattern:
        return "empty"
    # Canonicalize by trying all 8 dihedral orientations
    orientations = []
    curr = pattern
    for _ in range(4):
        orientations.append(curr)
        orientations.append([list(reversed(row)) for row in curr]) # flip
        # rotate 90
        curr = [list(row) for row in zip(*curr[::-1])]
        
    def pattern_to_str(p):
        return "".join("".join(str(cell != 0) for cell in row) for row in p)
        
    best = min(pattern_to_str(o) for o in orientations)
    return hashlib.md5(best.encode()).hexdigest()[:12]

def _get_location_hint(bbox: Tuple[int, int, int, int], rows: int, cols: int) -> str:
    min_r, min_c, max_r, max_c = bbox
    v_pos = "center"
    if min_r == 0: v_pos = "top"
    elif max_r == rows - 1: v_pos = "bottom"
    
    h_pos = ""
    if min_c == 0: h_pos = "left"
    elif max_c == cols - 1: h_pos = "right"
    
    if v_pos in ("top", "bottom") and h_pos:
        return f"corner_{v_pos[0]}{h_pos[0]}"
    elif v_pos != "center":
        return f"edge_{v_pos}"
    elif h_pos:
        return f"edge_{h_pos}"
    return "center"

def _are_adjacent(cells_a: Set[Tuple[int, int]], cells_b: Set[Tuple[int, int]]) -> bool:
    """Check if any cell in A is an 8-neighbor of any cell in B."""
    # For large regions, bbox check first
    for r, c in cells_a:
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0: continue
                if (r + dr, c + dc) in cells_b:
                    return True
    return False

def _contains(bbox_a: Tuple[int, int, int, int], bbox_b: Tuple[int, int, int, int]) -> bool:
    """Check if bbox_a strictly contains bbox_b."""
    ar1, ac1, ar2, ac2 = bbox_a
    br1, bc1, br2, bc2 = bbox_b
    return ar1 <= br1 and ac1 <= bc1 and ar2 >= br2 and ac2 >= bc2 and (bbox_a != bbox_b)
