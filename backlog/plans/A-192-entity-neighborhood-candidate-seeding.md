# Plan: A192 — Seed Candidate Generation From Entity-Neighborhood Graph Evidence

## Card metadata

- ID: A192
- Priority: P2
- Layer: ARC runtime
- Dependencies: A175, A177, A185

## Summary

`entity_ref` (A175's stable cross-frame entity identity) is available in `perceive.py`'s entity attributes but is silently dropped by `plan_generator.py::_click_targets` before it reaches candidate construction. As a result, the only entity-scoped graph query in the runtime (`fetch_entity_history`) is used once, for a flat goal-confidence bump — the planner itself never asks the graph "what do we already know about the specific object this click would target." This card threads `entity_ref` through to candidate metadata and adds a graph-backed entity-neighborhood evidence source to click-target scoring, client-side first (degrading cleanly until the server tool in the companion handoff doc lands).

## Technical approach

### 1. Thread `entity_ref` through `_click_targets`

`agents/arc4/plan_generator.py::_click_targets` (~line 672-725) iterates `perception.entities`, reading `entity.attributes` (`attrs`) for `coverage`/`cell_count`/`centroid`, but the returned target dict (~line 715-722) only keeps `x`, `y`, `entity_kind`, `entity_color`. Add `entity_ref`:

```python
scored.append(
    (
        salience,
        {
            "x": x,
            "y": y,
            "entity_kind": entity.kind,
            "entity_color": entity.value,
            "entity_ref": attrs.get("entity_ref"),
        },
    )
)
```

`attrs.get("entity_ref")` may be `None` for entities perceived before A175's correspondence assignment ran (defensive — should not happen in practice post-A175, but don't assume).

### 2. Thread `entity_ref` into candidate metadata

In `_build_candidates` (~line 194-300), `target_info` (the third element of each `target_variants` tuple) already flows into candidate metadata via `**target_info` (line 299: `**target_info,`). Since `target_info` is `dict(target)` from `_click_targets`'s output (line 206: `dict(target)`), step 1 alone is sufficient for `entity_ref` to reach `metadata["entity_ref"]` — no separate threading needed here. Confirm this during implementation (read the exact current line numbers first) rather than assuming.

### 3. Add `graph_queries.py::fetch_entity_neighborhood`

Follow the existing degrade-on-`capability_missing` pattern used by `fetch_rules_for_action` (`graph_queries.py:188-209`) and `fetch_causal_path` (`graph_queries.py:157-172`):

```python
def fetch_entity_neighborhood(self, entity_ref: Any) -> dict[str, Any]:
    """A192: live hypotheses/mechanics the graph already associates with a
    specific entity -- the neighborhood-inspection step of the graph-guided
    investigation loop, entity-scoped rather than action-family-scoped."""
    result = self._call_tool("fetch_entity_neighborhood", {"task_id": self.task_id, "entity_ref": entity_ref})
    if not isinstance(result, Mapping) or result.get("status") == "capability_missing":
        return {"hypotheses": [], "mechanics": []}
    hypotheses = result.get("hypotheses", [])
    mechanics = result.get("mechanics", [])
    return {
        "hypotheses": list(hypotheses) if isinstance(hypotheses, (list, tuple)) else [],
        "mechanics": list(mechanics) if isinstance(mechanics, (list, tuple)) else [],
    }
```

Add `"fetch_entity_neighborhood": "arc_get_entity_neighborhood"` to the tool-name mapping near the top of `graph_queries.py` (same table `fetch_entity_history`/`fetch_causal_path` are registered in, ~line 21-36).

**Do not** add `fetch_entity_neighborhood` to `GraphQueryPort` in `ports.py`. `fetch_entity_history` and `fetch_rules_for_action` are both deliberately left off that Protocol and accessed via `getattr(graph_port, "fetch_entity_history", None)` / `getattr(graph_port, "fetch_rules_for_action", None)` at their call sites (`goal_resolver.py:119`, `plan_generator.py:184`) — this is the established convention for a capability still rolling out server-side. Match it exactly:

```python
fetch_neighborhood = getattr(graph_port, "fetch_entity_neighborhood", None)
```

### 4. Consume it in `_build_candidates`

Inside the `for book_id, payload, target_info in target_variants:` loop (only reachable when `action_id == "ACTION6"` and `target_info` carries real entity data, not the `entity_kind: "fallback"` sentinel), after the existing `fetch_rules_for_action` block, add an entity-scoped evidence lookup gated on `target_info.get("entity_ref")` being present and `graph_port` being available:

```python
entity_ref = target_info.get("entity_ref")
if entity_ref is not None and graph_port is not None:
    fetch_neighborhood = getattr(graph_port, "fetch_entity_neighborhood", None)
    if fetch_neighborhood is not None:
        try:
            neighborhood = fetch_neighborhood(entity_ref)
            live_hypotheses = [h for h in neighborhood.get("hypotheses", []) if not h.get("falsified")]
            if live_hypotheses:
                score += max(h.get("confidence", 0.0) for h in live_hypotheses) * self._limits.entity_neighborhood_weight
        except Exception:
            pass
```

Add `entity_neighborhood_weight: float = <tunable default, mirror rule_confidence_weight's magnitude>` to `PlanGeneratorLimits`. Confirm the exact class/field name and existing defaults (e.g. `rule_confidence_weight`) by reading `plan_generator.py`'s limits dataclass before adding, and pick a comparably-scoped default rather than guessing a number without precedent.

This must be additive only — it must never *subtract* score or veto a candidate on its own (that's the vetter's job, and A191 already handles falsified-candidate exclusion). An empty or missing neighborhood must produce the exact same score as today.

### 5. Companion handoff doc

Write `docs/handoff/B278-entity-neighborhood-query.md` following the exact structure of `docs/handoff/B278-rules-as-nodes.md` (the template A177 used for its own new-tool ask): Summary, the tool signature being asked for (`arc_get_entity_neighborhood`, request/response shape), suggested schema (what graph edges would need to exist — see below), ARC-side status (what's already shipped, no action needed from hippocampy on that half), and how ARC will confirm it's fixed.

Schema note for the handoff doc: the architecture's documented per-game schema (`ARCHITECTURE.md`'s starter schema) has `(:Effect)-[:SUPPORTS|CONTRADICTS]->(:Hypothesis)` and `(:Object)-[:MOVED|...]->(:Object)`, but no direct edge from an `Object`/`GridEntity` to the `Hypothesis`/`Mechanic` nodes describing it. The new tool likely needs either (a) a new direct edge type linking `GridEntity` to the `Hypothesis`/`Rule` nodes derived from effects observed on that entity (natural extension of A176's `Transition` nodes, which A177's `Rule` nodes already reference), or (b) a traversal through existing `Transition`/`ActionEffect` nodes already scoped by entity (per `arc_get_entity_movement`'s existing `MOVED_BY` edge pattern) out to whatever `Rule`/`Hypothesis` nodes reference those same transitions. Flag both options in the handoff doc; let hippocampy's owner choose based on what's cheapest given the existing schema, per this repo's own non-negotiable that graph internals belong to hippocampy, not ARC.

Also flag, as a **separate, non-blocking note** in the handoff doc (not part of this card's own scope): while tracing `fetch_causal_path`'s server-side implementation to understand existing entity/action query patterns, its Cypher (`hippocampy/campy/brain/thalamus/tools/arc_queries.py::arc_get_causal_path`) matches `ActionFact {task_id, action_id}` by bare `action_id` only — no coordinate/book_id granularity. This is the same family-vs-instance conflation A185/A188 fixed client-side for `ACTION6`, but server-side and unfixed. Worth hippocampy's awareness independent of this card; do not fold a fix for it into this card's scope.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/plan_generator.py` | `_click_targets` includes `entity_ref` in target dict; `_build_candidates` consumes `fetch_entity_neighborhood` via `getattr` for click-target candidates; `PlanGeneratorLimits` gains `entity_neighborhood_weight` |
| `agents/arc4/graph_queries.py` | New `fetch_entity_neighborhood(entity_ref)`, registered in the tool-name map |
| `docs/handoff/B278-entity-neighborhood-query.md` (new) | Server-side tool ask, schema options, ARC-side status, verification steps |
| `tests/test_a192_entity_neighborhood_candidate_seeding.py` (new) | Coverage (see Tests) |

## Tests

New `tests/test_a192_entity_neighborhood_candidate_seeding.py`:

1. `_click_targets` includes `entity_ref` in its returned target dicts when `entity.attributes["entity_ref"]` is set; returns `None` for it when absent (defensive case, should not occur post-A175 but must not crash).
2. `fetch_entity_neighborhood` on a mock port returning `{"status": "capability_missing"}` yields `{"hypotheses": [], "mechanics": []}`, not an exception.
3. `_build_candidates` for an `ACTION6` candidate whose entity has a live (unfalsified) hypothesis in a mock port's `fetch_entity_neighborhood` response gets a score boost proportional to that hypothesis's confidence.
4. `_build_candidates` for the same scenario but with a mock port lacking `fetch_entity_neighborhood` entirely (simulating a pre-A192-aware port/stub) produces an identical score to today — regression guard, confirms `getattr(..., None)` degrades silently.
5. `_build_candidates` for an `ACTION6` candidate with `target_info.get("entity_ref") is None` (fallback click target case, `entity_kind: "fallback"`) never attempts the neighborhood lookup.
6. Non-click actions (`ACTION1`-`ACTION5`) are byte-for-byte unaffected — no entity anchor, no neighborhood call, identical candidates to pre-A192 behavior.

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a192_entity_neighborhood_candidate_seeding.py -v
make test-a
make test-all
```

Live confirmation: best-effort, gated on the server-side tool from the companion handoff doc landing — this card's client-side half must independently pass all tests and preserve current behavior even while the server tool is still `capability_missing`, per the established non-strict-MCP rollout policy (see A177's handoff for precedent: shipped client-side complete and correct before its server tools existed).

## Assumptions/defaults

- `entity_neighborhood_weight`'s default value has no strong prior — pick something comparable in magnitude to `rule_confidence_weight` (read its actual value from `PlanGeneratorLimits` before choosing, don't guess a number independently) and note in the Resolution that it's a starting point, not a tuned constant.
- This card's client-side changes must ship correct and inert (identical behavior) even if the server-side tool never lands, consistent with every prior B278-dependent card in this backlog (A177, A179, A186 all shipped client-side first).
- The `arc_get_causal_path` book_id-granularity note belongs in the handoff doc as an FYI, not as new acceptance criteria for this card — resist scope creep into fixing it here.
