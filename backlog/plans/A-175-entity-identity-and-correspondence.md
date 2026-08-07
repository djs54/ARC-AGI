# Plan: A175 — Entity Identity and Frame-to-Frame Correspondence

## Context

`entity_id = f"{task_id}_e{ent.get('color_id', 0)}_{ent.get('region_index', 0)}"` on the server always resolves to `_e0_0` because the client never sends `color_id`/`region_index` — confirmed by reading `agents/arc4/graph_queries.py::_serialize_entity` against the real `GridEntity` schema and the real `ingest_perception` handler. This collapses every entity in a task into one degenerate node and leaves `MOVED_BY` permanently unwritten, which is why `arc_get_causal_path` (consumed by A163) always returns `path_exists: False`.

## Step 0: Decide the correspondence-matching approach (gate)

Before implementing, decide:

1. **Simple bounded-radius nearest-centroid matching** (same color, centroid within N cells of the previous step's entity of that color) — cheap, deterministic, handles the common case (an object moves a few cells per action) well; can mismatch when multiple same-color objects are close together or an object moves further than the radius in one step.
2. **Bipartite matching over all same-color candidates** (e.g. minimum-cost assignment between this step's and last step's same-color entities, weighted by centroid distance) — more robust to the multi-object-of-same-color case, more implementation complexity.

Recommendation: start with (1) — it's a direct, testable win and matches the complexity of A170's diffing work. Escalate to (2) only if live-smoke testing reveals systematic misassignment (visible as implausible `MOVED_BY` deltas once the server side is live). Don't over-build before there's evidence (1) is insufficient.

## Implementation

### 1. Correspondence matching in `perceive.py`

Add a `WorkflowState`-scoped cache of the previous step's entities (mirroring A170's `previous_grid` pattern — ephemeral, unserialized). For each newly-extracted entity, search the prior step's entities of the same color within a bounded centroid-distance radius (configurable, start conservative); on a match, carry forward its stable identifier; on no match, mint a fresh one (e.g. an incrementing per-task counter, not raster-scan position).

### 2. Update `_serialize_entity`

Send the color as `color_id` and the stable correspondence identifier under whatever field name the *current* server code actually reads (re-verify against `campy/brain/thalamus/tools/arc_queries.py::ingest_perception` at implementation time — this plan describes the field as `region_index` based on the 2026-08-07 audit, but confirm it hasn't changed).

### 3. Hand-off doc

`docs/handoff/B278-entity-identity-and-moved-by.md`: reproduction (the exact `entity_id` collapse), the schema fields involved, and the specific ask — accept corrected identity fields, and write `MOVED_BY` when a `GridEntity` merge finds an existing node whose centroid differs from the incoming value (comparing before/after the `SET` on the same `MERGE`).

## Tests

New `tests/test_a175_entity_identity_correspondence.py`:

1. `test_same_entity_matched_across_steps` — an entity present in step N and step N+1 at a nearby position (same color) gets the same stable identifier both times.
2. `test_disappeared_and_new_entity_not_confused` — an entity vanishes and a different, similarly-colored entity appears elsewhere (beyond the matching radius) — assert they get *different* identifiers, not falsely merged.
3. `test_serialize_entity_sends_color_id_and_correspondence_id` — recording-stub `brain_client`, assert `ingest_perception`'s payload includes the corrected field names.
4. `test_multiple_same_color_entities_do_not_cross_match` — two same-color entities both present in consecutive steps, close to each other — document current known-limitation behavior explicitly (this is where Step 0's simple approach can misfire; the test should assert *some* deterministic, non-crashing outcome and note if it's not always correct, rather than silently passing on an untested edge case).

## Verify

```bash
.venv/bin/python -m pytest tests/test_a175_entity_identity_correspondence.py -v
make test-a
make test-all
```

Live confirmation (client-side only — server-side `MOVED_BY` write depends on the hand-off landing in hippocampy):
```bash
CAMPY_MCP_CMD="../hippocampy/.venv/bin/python -m campy.adapters.mcp_server" \
  PYTHONPATH=. .venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 15
```
Extract two consecutive `ingest_perception` request payloads and confirm: `color_id` is non-zero/matches the real entity color, and the same physical entity (by visual inspection of the grid_text/diff) carries the same correspondence id across the two steps.

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/perceive.py` | Frame-to-frame entity correspondence matching |
| `agents/arc4/graph_queries.py` | `_serialize_entity` sends corrected field names |
| `docs/handoff/B278-entity-identity-and-moved-by.md` | New hand-off doc |
| `tests/test_a175_entity_identity_correspondence.py` | New, 4 tests |

## Risks

- Matching-radius mistuning is the main risk (too small: legitimate movement looks like a new entity; too large: distinct nearby entities collapse). Start conservative, adjust from live-smoke evidence rather than guessing a "correct" value upfront.
- Server-side `MOVED_BY` write is out of this repo's implementable scope — this plan's acceptance is necessarily partial (client-side correctness + hand-off) until hippocampy lands its half.
