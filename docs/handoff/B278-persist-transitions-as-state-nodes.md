# Handoff: B278 New tools needed — persist observed transitions as State Nodes

**For:** hippocampy / Campy owner (B278 owns brain internals; ARC consumes across the MCP seam)
**From:** ARC_AGI A176 strategic review (2026-08-07)
**Status:** ARC-side client (write call + consumer query) shipped; new server-side tools needed

## Summary

A170 (2026-08-06) computes a real, structured before/after diff for every action: which cells changed, from what color to what. It's evaluator-tested, LLM-prompt-tested, working evidence — but it's entirely ephemeral, discarded after one prompt. Nothing in the graph persists it, so the one component in this architecture designed to accumulate evidence across steps and across replays never sees the evidence that actually describes what actions *do*.

This is exactly the "State Node" pattern (external material reviewed 2026-08-07, Modern Data 101 *"Knowledge Graph for AI"*) — nodes that make temporal change explicit and queryable: *"not just what is, but what changed, when, from what, and why."*

## Ask: two new tools

### 1. `arc_record_transition` (write)

Called once per step (when a real change occurred) with:

```json
{
  "task_id": "...",
  "step": 4,
  "action_id": "ACTION6",
  "changed_count": 3,
  "color_transitions": [{"from": 2, "to": 5, "count": 3}],
  "entity_ref": 1
}
```

`entity_ref` is the A175 stable correspondence id (may be `null` if the changed cells didn't fall within a known entity's bounding box — that's a legitimate, expected case, not an error). `color_transitions` is a bounded histogram (grouped by `from`/`to` color pair), not one row per changed cell — deliberately coarser than A170's own per-cell cap, to keep node/edge growth bounded (see backlog/A176.md Step 0 for the granularity decision and rationale).

Suggested persistence: a `Transition` node per call (or per distinct `(action_id, entity_ref)` pair, `MERGE`d and incrementally updated — your call on which fits the existing schema patterns better), with an edge to the `GridEntity` identified by `entity_ref` (once A175's hand-off — `docs/handoff/B278-entity-identity-and-moved-by.md` — lands and `entity_ref` reliably maps to a real, stable `GridEntity`).

### 2. `arc_get_entity_history` (read)

```json
{"task_id": "...", "entity_ref": 1}
```

Returns:

```json
{"transitions": [{"action_id": "ACTION6", "step": 4, "color_transitions": [...]}], "changed_count_total": 3}
```

*"What has happened to this entity across the game so far"* — the direct consumer query.

## ARC-side status (no action needed from you on this half)

- `agents/arc4/graph_queries.py::record_transition` — summarizes A170's diff into the histogram shape above, attributes it to the entity whose bbox contains the most changed cells (best-overlap heuristic, not exact), calls `arc_record_transition`.
- `agents/arc4/evaluator.py::_record_transition` — wired into the evaluate phase, calling the above whenever a real diff exists.
- `agents/arc4/graph_queries.py::fetch_entity_history` — calls `arc_get_entity_history`, degrades gracefully to `{"transitions": [], "changed_count_total": 0}` on `capability_missing`.
- `agents/arc4/goal_resolver.py::_tier_one_hypotheses` — **the consumer**: fetches entity history per candidate goal entity and boosts confidence when the entity has a real prior transition history (evidence it's genuinely interactive, not just present). This is live and tested against the current (missing-tool) server state — it degrades to "no boost" cleanly, and will start actually mattering the moment these two tools exist.

Until these tools exist, every `record_transition`/`fetch_entity_history` call returns `capability_missing`/an empty history via the existing `strict=False` degradation path — confirmed live, not an error.

## How ARC will know it's fixed

Run `--live-smoke`, capture a step where a real grid change occurred, confirm `record_transition`'s call no longer returns `capability_missing`. Then confirm a later step's goal resolution shows a confidence boost (`"entity_history:has_changed"` in a hypothesis's `evidence` tuple) for an entity that has genuinely changed before.
