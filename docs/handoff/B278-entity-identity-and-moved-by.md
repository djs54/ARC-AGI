# Handoff: B278 `GridEntity` identity collapses to one node per task; `MOVED_BY` never written

**For:** hippocampy / Campy owner (B278 owns brain internals; ARC consumes across the MCP seam)
**From:** ARC_AGI A175 strategic review (2026-08-07)
**Status:** ARC-side fix shipped (real `color_id`/`region_index` now sent); server-side `MOVED_BY` write still needed

## Summary

`arc_perceive_state` keys every `GridEntity` by:

```python
entity_id = f"{task_id}_e{ent.get('color_id', 0)}_{ent.get('region_index', 0)}"
```

Until this card, the ARC client never sent `color_id` or `region_index` at all — every entity's `entity_id` resolved to the literal default `"{task_id}_e0_0"`, so every entity ever observed in a task `MERGE`d onto **one node**, overwritten each step. `color_id`, `centroid_row/col`, `pixel_count` all ended up holding whatever the last-written entity happened to have.

Consequence: `MOVED_BY` (`FROM GridEntity TO ActionEffect, delta_row DOUBLE, delta_col DOUBLE`) has three readers (`arc_get_causal_path` ×2, `arc_get_entity_movement`) and — confirmed via `grep -rn "MOVED_BY" campy/` — **zero writers**, anywhere in the tree. `arc_get_causal_path`'s 4-hop query:

```cypher
MATCH (af:ActionFact)-[:DERIVED_FROM_FACT]->(ae:ActionEffect)
      <-[:MOVED_BY]-(ge:GridEntity)-[:REQUIRES_ENTITY]-(vc:VictoryCondition)
```

is exactly the right causal question ("does this action move an entity a victory condition requires?") and can never match a row as a result. This is why ARC_AGI's A163 (a confidence-threshold causal-override check, implemented correctly against the contract this session) is inert in production — same failure class as the A160-A167 field-mismatch bugs, one layer deeper.

## What's fixed on the ARC side (no action needed from you)

`agents/arc4/perceive.py` now computes frame-to-frame entity correspondence (bounded-radius nearest-centroid matching, same color) and assigns each entity a stable `entity_ref` that persists across steps for the same physical object — not raster-scan position, which shifts whenever an entity above disappears. `agents/arc4/graph_queries.py::_serialize_entity` now sends the real `color_id` (the entity's color) and `region_index` (the new stable `entity_ref`), plus `centroid_row`/`centroid_col`/`pixel_count`, matching exactly the flat fields `arc_perceive_state` reads.

## What's needed on the hippocampy side

`arc_perceive_state`'s current `MERGE`/`SET` on `GridEntity` only ever writes `color_id`, `centroid_row/col`, `pixel_count`, `inferred_role`, `last_updated_step` — it never compares the *incoming* centroid against whatever the *existing* node (same `entity_id`, now correctly stable) already had, and never writes `MOVED_BY`. The ask: when a `MERGE` finds an existing `GridEntity` node whose stored centroid differs from the incoming one, write a `MOVED_BY` edge from that `GridEntity` to the current step's `ActionEffect`, with `delta_row`/`delta_col` computed from the before/after centroids.

## Reproduction (raw MCP session, no ARC code)

```python
# Two calls, same task_id, entity genuinely moves between them
arc_perceive_state {
  task_id, step: 0, grid_hash: "...",
  entities: [{"color_id": 5, "region_index": 0, "centroid_row": 2.0, "centroid_col": 2.0, "pixel_count": 1}]
}
arc_perceive_state {
  task_id, step: 1, grid_hash: "...",
  entities: [{"color_id": 5, "region_index": 0, "centroid_row": 2.0, "centroid_col": 5.0, "pixel_count": 1}]
}
arc_get_entity_movement {task_id, step: 1}
```

### Currently observed

`arc_get_entity_movement` returns `{"entities": []}` — no `MOVED_BY` edge exists to match, regardless of how the centroid actually changed.

### Expected once fixed

`arc_get_entity_movement` returns an entry for `entity_id = "{task_id}_e5_0"` with `delta_col ≈ 3.0`.

## How ARC will know it's fixed

`tests/test_a175_entity_identity_correspondence.py` covers the client-side correctness (already passing). Once the server-side write lands, the closing signal is: run `--live-smoke`, capture two consecutive `arc_perceive_state` calls for the same task where a real entity moved, and confirm `arc_get_causal_path`/`arc_get_entity_movement` return non-default (`path_exists: true` / a real `MOVED_BY` entry) results for at least one step. Happy to add a `requires_mcp`-marked contract test in `tests/` mirroring A146's precedent once this is confirmed working, if useful.
