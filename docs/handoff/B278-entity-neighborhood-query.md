# Handoff: B278 New tool needed — entity-neighborhood graph query (hypotheses/mechanics attached to a specific object)

**For:** hippocampy / Campy owner (B278 owns brain internals; ARC consumes across the MCP seam)
**From:** ARC_AGI A192, graph-engineering review (2026-08-22)
**Status:** ARC-side client (entity_ref threading, consumer query, candidate-generation wiring) shipped and degrades cleanly; new server-side tool needed

## Summary

The graph-guided investigation loop (video-prompted review this session) has six steps: anchor on an entity, inspect its neighborhood in the graph, form hypotheses from adjacent edges, test them, support/contradict, repeat. Auditing every graph query the ARC runtime makes, step 2 — "inspect neighborhood" — doesn't really exist. The only entity-scoped query anywhere in `agents/arc4/graph_queries.py` is `fetch_entity_history(entity_ref)`, used once (`goal_resolver.py`) for a flat goal-confidence bump. Everything else is keyed by `action_id`, not by entity. The planner picks what to click based on action-family-level evidence ("what has ACTION6 done, ever") and coordinate-level attempt/falsification counts, never "does the graph already know something specific about the object at this coordinate."

A175 already built the anchor this needs — `entity_ref`, stable across frames — it just never reaches a query that uses it for anything but goal-confidence scoring.

## Ask: one new tool

### `arc_get_entity_neighborhood` (read)

```json
{"task_id": "...", "entity_ref": 3}
```

Returns:

```json
{
  "hypotheses": [{"hypothesis_id": "...", "claim": "...", "confidence": 0.6, "falsified": false}],
  "mechanics": [{"name": "...", "confidence": 0.5}]
}
```

Live (unfalsified) hypotheses and mechanics the graph already associates with this specific entity — the neighborhood the planner should inspect before deciding whether clicking it is worth trying.

## Suggested schema (two options, your call)

The documented per-game schema (`ARCHITECTURE.md`) has `(:Effect)-[:SUPPORTS|CONTRADICTS]->(:Hypothesis)` and `(:Object)-[:MOVED|EXPANDED|BLOCKED|APPROACHED_GOAL]->(:Object)`, but no direct edge from an `Object`/`GridEntity` to the `Hypothesis`/`Mechanic`/`Rule` nodes describing it. Two ways to close that:

1. **New direct edge**: link `GridEntity` to the `Hypothesis`/`Rule` nodes derived from effects observed on that entity — a natural extension of A176's `Transition` nodes (which A177's `Rule` nodes already reference via `CONFIRMED_BY`/`FALSIFIED_BY`).
2. **Traversal through existing nodes**: `GridEntity` already has `MOVED_BY` edges to `ActionEffect` (per `arc_get_entity_movement`'s existing query) — traverse from there out to whatever `Rule`/`Hypothesis` nodes reference those same effects/transitions, no new edge type needed, just a longer bounded-hop query.

Either is fine from ARC's side; pick whichever is cheaper given the current schema.

## Related, separate finding — not part of this ask

While tracing existing query patterns for this handoff, `arc_get_causal_path`'s Cypher (`campy/brain/thalamus/tools/arc_queries.py::arc_get_causal_path`) matches `ActionFact {task_id, action_id}` by bare `action_id` — no coordinate/book_id granularity:

```cypher
MATCH (af:ActionFact {task_id: $tid, action_id: $aid})-[:DERIVED_FROM_FACT]->(ae:ActionEffect)
      <-[:MOVED_BY]-(ge:GridEntity)-[:REQUIRES_ENTITY]-(vc:VictoryCondition {task_id: $tid})
```

For `ACTION6`, this is the same family-vs-instance conflation A185/A188 fixed client-side (a specific click coordinate's causal-path evidence is indistinguishable from every other coordinate sharing the `ACTION6` action_id). Flagging for your awareness — this is a separate, independent fix from the entity-neighborhood ask above, not bundled into A192's scope, and not something ARC can fix from its side since the query itself lives server-side.

## ARC-side status (no action needed from you on this half)

- `agents/arc4/perceive.py` — `entity_ref` already assigned per entity (A175), unchanged by this card.
- `agents/arc4/plan_generator.py::_click_targets` — now includes `entity_ref` in its returned target dicts.
- `agents/arc4/plan_generator.py::_build_candidates` — the consumer: an `ACTION6` candidate's score gets a small additive boost when its anchored entity has a live (unfalsified) hypothesis via `fetch_entity_neighborhood`, using the same `getattr(graph_port, "fetch_entity_neighborhood", None)` optional-capability pattern as `fetch_entity_history`/`fetch_rules_for_action`.
- `agents/arc4/graph_queries.py::fetch_entity_neighborhood` — calls `arc_get_entity_neighborhood`, degrades to `{"hypotheses": [], "mechanics": []}` on `capability_missing`, exactly like every other B278 consumer in this file.

Confirmed the new tool is currently absent (`capability_missing`) — the client degrades cleanly, not an error, consistent with the runtime's non-strict MCP rollout policy.

## How ARC will know it's fixed

Run a live smoke episode with a real click-target entity that has an existing confirmed rule/hypothesis from a prior step. Confirm `arc_get_entity_neighborhood` no longer returns `capability_missing`, and that the corresponding `ACTION6@x,y` candidate's score in the plan-phase telemetry shows a nonzero entity-neighborhood contribution distinct from its action-family-level (`fetch_rules_for_action`) contribution.
