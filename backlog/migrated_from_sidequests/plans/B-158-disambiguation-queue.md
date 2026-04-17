# Plan for B158 — Disambiguation Queue: Human-in-the-Loop Entity Curation

## Card Metadata

- **Card ID**: B158
- **Priority**: P3
- **Dependencies**: None

## Summary

Add a disambiguation queue that surfaces gray-zone entity pairs for human curation, plus resolution actions (merge/separate) that feed back into the Hebbian learning system. Two new MCP tools, one new node type (DisambiguationEvent), one new edge type (DISTINCT_FROM).

## Technical Approach

### 1. Schema additions in `schema.py`

```python
# New node type
"DisambiguationEvent": """
    event_id       STRING,
    concept_id_a   STRING,
    concept_id_b   STRING,
    similarity     DOUBLE,
    status         STRING,      -- pending | merged | separated | skipped
    resolved_at    TIMESTAMP,
    resolved_by    STRING,      -- "user" | "sweep"
    created_at     TIMESTAMP,
    PRIMARY KEY (event_id)
""",

# New relationship
"DISTINCT_FROM (FROM Concept TO Concept, created_at TIMESTAMP, source STRING)",
```

Add to `EXPECTED_TABLES` and `EXPECTED_RELS` in schema.py. The `ensure_schema()` function handles creation.

### 2. Create DisambiguationEvent on "uncertain" result

In `orchestrator.py`, in the uncertain path (~line 307):

```python
# After creating the new concept for the "uncertain" case:
elif arb["classification"] == "uncertain":
    new_id = _store_concept(...)
    # B158: Record disambiguation event for the queue
    _create_disambiguation_event(
        db, new_id, arb["referenced_node_ids"][0] if arb["referenced_node_ids"] else None,
        top["similarity"] if top else 0.0, now
    )

def _create_disambiguation_event(db, concept_id_a, concept_id_b, similarity, now):
    if not concept_id_a or not concept_id_b:
        return
    import uuid
    event_id = str(uuid.uuid4())
    db.execute(
        "CREATE (e:DisambiguationEvent {"
        "  event_id: $eid, concept_id_a: $a, concept_id_b: $b,"
        "  similarity: $sim, status: 'pending',"
        "  resolved_at: null, resolved_by: null, created_at: $now"
        "})",
        {"eid": event_id, "a": concept_id_a, "b": concept_id_b,
         "sim": similarity, "now": now}
    )
```

### 3. `get_disambiguation_queue` tool

```python
# In tools/__init__.py

async def get_disambiguation_queue(arguments, db, **kwargs):
    """Retrieve pending uncertain entity pairs for human curation."""
    limit = arguments.get("limit", 10)

    # Get pending events
    events = db.execute(
        "MATCH (e:DisambiguationEvent) "
        "WHERE e.status = 'pending' "
        "RETURN e.event_id, e.concept_id_a, e.concept_id_b, "
        "       e.similarity, e.created_at "
        "ORDER BY e.created_at DESC "
        "LIMIT $lim",
        {"lim": limit}
    )

    pairs = []
    for ev in events:
        # Fetch both concepts with 1-hop context
        concept_a = _get_concept_with_context(db, ev["concept_id_a"])
        concept_b = _get_concept_with_context(db, ev["concept_id_b"])
        if concept_a and concept_b:
            pairs.append({
                "event_id": ev["event_id"],
                "similarity": ev["similarity"],
                "created_at": str(ev["created_at"]),
                "concept_a": concept_a,
                "concept_b": concept_b,
                "shared_neighbors": _shared_neighbors(db, ev["concept_id_a"], ev["concept_id_b"]),
            })

    return {"pairs": pairs, "total_pending": len(pairs)}


def _get_concept_with_context(db, concept_id):
    """Get concept text + labels + neighbor count."""
    rows = db.execute(
        "MATCH (c:Concept {concept_id: $cid}) "
        "OPTIONAL MATCH (c)-[:HAS_ALT_LABEL]->(l:Label) "
        "RETURN c.concept_id, c.text_raw, c.gist_class, c.confidence, "
        "       c.pathway_strength, c.confidence_low, "
        "       collect(l.text) AS alt_labels",
        {"cid": concept_id}
    )
    return rows[0] if rows else None


def _shared_neighbors(db, cid_a, cid_b):
    """Find concepts that both A and B are connected to."""
    rows = db.execute(
        "MATCH (a:Concept {concept_id: $a})-[]->(n:Concept)<-[]-(b:Concept {concept_id: $b}) "
        "WHERE n.archived = false "
        "RETURN DISTINCT n.concept_id, n.text_raw "
        "LIMIT 10",
        {"a": cid_a, "b": cid_b}
    )
    return rows
```

### 4. `resolve_disambiguation` tool

```python
async def resolve_disambiguation(arguments, db, **kwargs):
    """Resolve a disambiguation pair: merge, separate, or skip."""
    event_id = arguments["event_id"]
    resolution = arguments["resolution"]  # "merge" | "separate" | "skip"

    if resolution not in ("merge", "separate", "skip"):
        return {"error": f"Invalid resolution: {resolution}. Use merge, separate, or skip."}

    # Get the event
    events = db.execute(
        "MATCH (e:DisambiguationEvent {event_id: $eid}) "
        "RETURN e.concept_id_a, e.concept_id_b, e.status",
        {"eid": event_id}
    )
    if not events:
        return {"error": f"Event {event_id} not found"}
    ev = events[0]
    if ev["status"] != "pending":
        return {"error": f"Event already resolved: {ev['status']}"}

    now = _now()
    cid_a, cid_b = ev["concept_id_a"], ev["concept_id_b"]

    if resolution == "merge":
        # Keep older concept as canonical, merge newer into it
        concepts = db.execute(
            "MATCH (a:Concept {concept_id: $a}), (b:Concept {concept_id: $b}) "
            "RETURN a.concept_id, a.created_at, a.text_raw, "
            "       b.concept_id, b.created_at, b.text_raw",
            {"a": cid_a, "b": cid_b}
        )
        if not concepts:
            return {"error": "One or both concepts not found"}
        c = concepts[0]

        # Determine canonical (older) vs duplicate (newer)
        canonical_id = cid_a if c["a.created_at"] <= c["b.created_at"] else cid_b
        duplicate_id = cid_b if canonical_id == cid_a else cid_a
        duplicate_text = c["b.text_raw"] if canonical_id == cid_a else c["a.text_raw"]

        # Create altLabel from duplicate's text
        label_id = str(uuid.uuid4())
        db.execute(
            "CREATE (l:Label {"
            "  label_id: $lid, text: $txt, label_type: 'alternative',"
            "  confidence: 0.95, source: 'user', language: 'en', created_at: $now"
            "})",
            {"lid": label_id, "txt": duplicate_text, "now": now}
        )
        # Embed the label
        from mcp_engine.graph.embeddings import embed
        emb = embed(duplicate_text)
        db.execute(
            "MATCH (l:Label {label_id: $lid}) SET l.embedding = $emb",
            {"lid": label_id, "emb": emb}
        )
        # Wire canonical -> altLabel
        db.execute(
            "MATCH (c:Concept {concept_id: $cid}), (l:Label {label_id: $lid}) "
            "CREATE (c)-[:HAS_ALT_LABEL {created_at: $now}]->(l)",
            {"cid": canonical_id, "lid": label_id, "now": now}
        )

        # Redirect edges from duplicate to canonical
        # (Named edges: REQUIRES, ENABLES, etc.)
        for rel_type in ["REQUIRES", "ENABLES", "REPLACES", "CONTRADICTS",
                         "PART_OF", "CHOSEN_OVER", "IMPLEMENTS", "EXTENDS",
                         "ALTERNATIVE_TO", "CO_OCCURS_WITH"]:
            db.execute(
                f"MATCH (dup:Concept {{concept_id: $dup}})-[r:{rel_type}]->(t:Concept) "
                f"WHERE t.concept_id <> $can "
                f"MERGE (can:Concept {{concept_id: $can}})-[:{rel_type} {{confidence: r.confidence, "
                f"  inferred_by: 'merge', inferred_at: $now}}]->(t) ",
                {"dup": duplicate_id, "can": canonical_id, "now": now}
            )

        # Archive duplicate
        db.execute(
            "MATCH (c:Concept {concept_id: $cid}) "
            "SET c.archived = true",
            {"cid": duplicate_id}
        )

        # Boost canonical
        db.execute(
            "MATCH (c:Concept {concept_id: $cid}) "
            "SET c.pathway_strength = c.pathway_strength + 0.15, "
            "    c.confidence_low = false, "
            "    c.last_accessed_at = $now",
            {"cid": canonical_id, "now": now}
        )

        result_msg = f"Merged: '{duplicate_text}' → altLabel of canonical concept"

    elif resolution == "separate":
        # Confirm both are distinct — set confident, create DISTINCT_FROM
        db.execute(
            "MATCH (a:Concept {concept_id: $a}), (b:Concept {concept_id: $b}) "
            "SET a.confidence_low = false, b.confidence_low = false "
            "CREATE (a)-[:DISTINCT_FROM {created_at: $now, source: 'user'}]->(b)",
            {"a": cid_a, "b": cid_b, "now": now}
        )
        result_msg = "Separated: both concepts confirmed as distinct entities"

    else:  # skip
        result_msg = "Skipped: pair re-queued for later review"

    # Update event status
    final_status = resolution if resolution != "skip" else "pending"
    db.execute(
        "MATCH (e:DisambiguationEvent {event_id: $eid}) "
        "SET e.status = $status, e.resolved_at = $now, e.resolved_by = 'user'",
        {"eid": event_id, "status": final_status if final_status != "pending" else "pending",
         "now": now}
    )

    return {"result": result_msg, "resolution": resolution}
```

### 5. Step 5 DISTINCT_FROM check

In `step5_retrieval.py`, after retrieving candidates, filter out any that have a DISTINCT_FROM edge with the incoming concept's nearest match:

```python
def retrieve_candidates(embedding, exclude_id, db, limit=5, exclude_ids=None):
    # ... existing vector search ...

    # B158: Filter out candidates that are DISTINCT_FROM recently created concepts
    # (Only applies if we have exclude_ids to check against)
    if exclude_ids and hits:
        distinct_pairs = _get_distinct_pairs(db, exclude_ids)
        hits = [h for h in hits if h["concept_id"] not in distinct_pairs]

    return hits

def _get_distinct_pairs(db, concept_ids):
    """Get concept IDs that are DISTINCT_FROM any of the given IDs."""
    if not concept_ids:
        return set()
    rows = db.execute(
        "MATCH (a:Concept)-[:DISTINCT_FROM]-(b:Concept) "
        "WHERE a.concept_id IN $ids "
        "RETURN b.concept_id",
        {"ids": concept_ids}
    )
    return {r["b.concept_id"] for r in rows}
```

### 6. Tool schemas

```python
# In tool_schemas.py
{
    "name": "get_disambiguation_queue",
    "description": "Get pending entity disambiguation pairs for human review. Returns gray-zone concept pairs that the system couldn't automatically resolve.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Max pairs to return", "default": 10}
        }
    }
},
{
    "name": "resolve_disambiguation",
    "description": "Resolve a disambiguation pair: merge (same entity), separate (distinct entities), or skip (review later).",
    "inputSchema": {
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "DisambiguationEvent ID from get_disambiguation_queue"},
            "resolution": {"type": "string", "enum": ["merge", "separate", "skip"], "description": "How to resolve: merge=same entity, separate=distinct, skip=review later"}
        },
        "required": ["event_id", "resolution"]
    }
}
```

## Concrete File Changes

| File | Change |
|------|--------|
| `mcp_engine/schema.py` | Add DisambiguationEvent node type, DISTINCT_FROM relationship |
| `mcp_engine/loop/orchestrator.py` | Create DisambiguationEvent on "uncertain" arbitration result |
| `mcp_engine/loop/step5_retrieval.py` | Filter candidates with DISTINCT_FROM edges |
| `mcp_engine/tools/__init__.py` | Add `get_disambiguation_queue` and `resolve_disambiguation` handlers |
| `mcp_engine/tool_schemas.py` | Add schemas for both new tools |
| `docs/tool-catalog.md` | Document both new tools |
| `tests/test_disambiguation_queue.py` | NEW: test queue, merge, separate, DISTINCT_FROM filtering |
| Adapter allow-lists | Propagate new tools |

## API/Schema/Test Updates

- Two new MCP tools: `get_disambiguation_queue`, `resolve_disambiguation`
- One new node type: `DisambiguationEvent`
- One new edge type: `DISTINCT_FROM`
- Adapter allow-lists must include both new tools
- `docs/tool-catalog.md` must document both tools

## Validation Commands

```bash
python3 -m pytest tests/test_disambiguation_queue.py -v
python3 -m pytest tests/test_adapters.py -q
rg -n "TOOL_HANDLERS|TOOLS:" mcp_engine/tool_schemas.py mcp_engine/tools/__init__.py adapters/
```

## Risks / Constraints

- **Edge redirection on merge**: Redirecting all edge types from duplicate to canonical is complex. If a new edge type is added later and not included in the redirect list, merged concepts may lose edges. Mitigation: use a dynamic query that finds all outgoing edges rather than a hardcoded type list.
- **Concurrent arbitration**: If two conversations simultaneously produce "uncertain" for the same concept pair, two DisambiguationEvents may be created. Mitigation: dedup on (concept_id_a, concept_id_b) pair when querying the queue.
- **Queue growth**: If the user never reviews the queue, DisambiguationEvents accumulate. The background sweep could auto-resolve old events (>30 days) by defaulting to "separate" — but this is a future enhancement.

## Done When

- DisambiguationEvent created on every "uncertain" arbitration result
- Queue tool returns pairs with graph context
- Merge creates altLabel + redirects edges + archives duplicate
- Separate creates DISTINCT_FROM edge + sets both confident
- Step 5 respects DISTINCT_FROM to avoid re-proposing separated pairs
- New tools in adapter allow-lists and tool-catalog.md
- All tests pass
