# Plan: A-102 — Object mechanic graph extraction

## Card metadata

- **Card:** A102
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A050, A073, A101

## Summary

Build a deterministic object mechanic graph from each frame so action evaluation can operate on objects and relations instead of raw pixel diffs.

## Implementation approach

1. Add `agents/arc3/mechanic_graph.py` with:
   - `MechanicObject`: `id`, `signature`, `color`, `shape_kind`, `bbox`, `centroid`, `area`, `confidence`, `props`.
   - `MechanicRelation`: `src`, `rel`, `dst`, `confidence`, `evidence_path_ids`, `props`.
   - `MechanicGraphExtractor.extract(grid, frame_hash, step) -> MechanicGraphSnapshot`.
2. Use existing connected-component / scene-graph helpers where available.
3. Add shape classifiers:
   - `ring_or_portal`: hollow compact loop.
   - `hub_or_token`: compact filled colored square/cluster.
   - `endpoint_or_anchor`: small gray/white marker with colored center.
   - `spoke_or_link`: thin line-like component connecting nodes.
   - `terrain_or_region`: large background/obstacle component.
4. Add relation builders:
   - `MATCHES_COLOR`
   - `CONNECTED_TO`
   - `NEAR`
   - `INSIDE_OR_OVERLAPS`
   - `CANDIDATE_TARGET`
   - `ANCHORS`
5. Persist snapshots into `WorldModelGraph`:
   - `(:Object {signature, color, shape_kind, step})`
   - `(:Object)-[:MATCHES_COLOR]->(:Object)`
   - `(:Object)-[:CONNECTED_TO {via}]->(:Object)`
6. Add a compact prompt summary method capped to top objects and high-confidence relations.

## Concrete file additions/edits

- Add `agents/arc3/mechanic_graph.py`
- Edit `agents/arc3/scene_graph.py`
- Edit `agents/arc3/world_model.py`
- Edit `agents/arc3/orchestrator.py`
- Add `tests/test_a102_object_mechanic_graph_extraction.py`

## API/interface changes

`WorldModelGraph` should add:

```python
def apply_mechanic_graph_snapshot(self, snapshot: MechanicGraphSnapshot) -> None: ...
def get_current_mechanic_graph_summary(self, max_objects: int = 12, max_edges: int = 24) -> dict: ...
```

## Tests to add or run

```bash
.venv/bin/python -m pytest -q tests/test_a102_object_mechanic_graph_extraction.py tests/test_scene_graph.py
.venv/bin/python -m pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- This is an in-memory labeled property graph enrichment.
- Use stable signatures derived from color, shape, relative geometry, and local topology; avoid raw step ids as object identity.
- Keep extraction graph-local and deterministic; no LLM or MCP calls.
