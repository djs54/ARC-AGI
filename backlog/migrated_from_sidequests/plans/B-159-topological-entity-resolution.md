# Plan for B159 — Topological Entity Resolution: Graph-Neighborhood Overlap in Step 5

## Card Metadata

- **Card ID**: B159
- **Priority**: P4
- **Dependencies**: None

## Summary

Enhance Step 5 candidate retrieval to consider graph-neighborhood overlap as a secondary signal. Concepts with low text similarity but high structural overlap (shared neighbors via named edges and CO_OCCURS_WITH) get promoted into the gray zone for Step 6 arbitration. This catches entity matches that pure vector similarity misses.

## Technical Approach

### 1. Lower the vector search floor for topological check

In `step5_retrieval.py`, the current flow fetches candidates above `MATCH_THRESHOLD` (0.75). To enable topological matching, we need candidates in the 0.55-0.75 range too.

```python
MATCH_THRESHOLD      = 0.75   # unchanged — still the final threshold
TOPO_SEARCH_FLOOR    = 0.55   # lower floor for topological check candidates
TOPO_JACCARD_WEIGHT  = 0.30   # how much topological overlap boosts similarity
MIN_JACCARD_BOOST    = 0.50   # minimum Jaccard to apply any boost

def retrieve_candidates(embedding, exclude_id, db, limit=5, exclude_ids=None):
    try:
        raw = db.vector_search("Concept", "concept_emb_idx", embedding,
                               limit + _FETCH_HEADROOM)
    except Exception:
        _logger.exception("Vector search failed")
        return []

    # Exclude self, archived, and already-processed
    all_exclude = {exclude_id}
    if exclude_ids:
        all_exclude.update(exclude_ids)

    hits = []
    topo_candidates = []  # Sub-threshold candidates for topological check

    for row in raw:
        cid = row["concept_id"]
        if cid in all_exclude:
            continue
        if row.get("archived", False):
            continue
        sim = row["similarity"]

        if sim >= MATCH_THRESHOLD:
            hits.append(row)
        elif sim >= TOPO_SEARCH_FLOOR:
            topo_candidates.append(row)

    # B159: Check topological overlap for sub-threshold candidates
    if topo_candidates and exclude_ids:
        # Get 1-hop neighbors of the incoming concept (via its nearest strong match
        # or via exclude_ids which represent concepts from same message)
        incoming_neighbors = _get_neighbor_set(db, exclude_ids)

        for candidate in topo_candidates:
            candidate_neighbors = _get_neighbor_set(db, [candidate["concept_id"]])
            jaccard = _jaccard_similarity(incoming_neighbors, candidate_neighbors)

            if jaccard >= MIN_JACCARD_BOOST:
                # Boost effective similarity
                boosted_sim = candidate["similarity"] + (jaccard * TOPO_JACCARD_WEIGHT)
                if boosted_sim >= MATCH_THRESHOLD:
                    candidate["similarity"] = boosted_sim
                    candidate["topo_boosted"] = True
                    candidate["jaccard_overlap"] = jaccard
                    hits.append(candidate)
                    _logger.info(
                        "B159: Topological boost: '%s' (%.2f→%.2f, jaccard=%.2f)",
                        candidate["text_raw"], candidate["similarity"] - (jaccard * TOPO_JACCARD_WEIGHT),
                        boosted_sim, jaccard
                    )

    hits.sort(key=lambda x: x["similarity"], reverse=True)
    return hits[:limit]
```

### 2. Neighbor set computation

```python
def _get_neighbor_set(db, concept_ids: list[str]) -> set[str]:
    """Get the set of 1-hop neighbor concept_ids for a list of concepts.

    Considers named edges (REQUIRES, ENABLES, etc.) and strong CO_OCCURS_WITH (count >= 3).
    """
    if not concept_ids:
        return set()

    rows = db.execute(
        "MATCH (c:Concept)-[r]->(n:Concept) "
        "WHERE c.concept_id IN $ids "
        "  AND n.archived = false "
        "  AND n.concept_id NOT IN $ids "
        "RETURN DISTINCT n.concept_id",
        {"ids": concept_ids}
    )
    named_neighbors = {r["n.concept_id"] for r in rows}

    # Also include strong CO_OCCURS_WITH neighbors (count >= 3)
    cooccur_rows = db.execute(
        "MATCH (c:Concept)-[r:CO_OCCURS_WITH]->(n:Concept) "
        "WHERE c.concept_id IN $ids "
        "  AND r.count >= 3 "
        "  AND n.archived = false "
        "  AND n.concept_id NOT IN $ids "
        "RETURN DISTINCT n.concept_id",
        {"ids": concept_ids}
    )
    cooccur_neighbors = {r["n.concept_id"] for r in cooccur_rows}

    return named_neighbors | cooccur_neighbors


def _jaccard_similarity(set_a: set, set_b: set) -> float:
    """Jaccard similarity: |intersection| / |union|."""
    if not set_a and not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0
```

### 3. Performance considerations

Each topological check requires 1-2 Cypher queries per candidate. To keep latency acceptable:

- Only check candidates in the 0.55-0.75 range (typically 0-5 candidates)
- Only check if `exclude_ids` is provided (meaning we have context about the incoming concept's neighborhood)
- Cache the incoming concept's neighbor set (computed once, reused for all candidates)
- Total added latency: ~10-50ms for typical cases (1-5 candidates × 1 query each)

### 4. No changes to existing thresholds

The 0.75 and 0.92 thresholds are unchanged. The boosted similarity is used for the gray zone check, but the original similarity is preserved in the candidate dict for Step 6 to reference. The `topo_boosted` flag tells Step 6 that topological overlap contributed to the match.

## Concrete File Changes

| File | Change |
|------|--------|
| `mcp_engine/loop/step5_retrieval.py` | Add `TOPO_SEARCH_FLOOR`, `TOPO_JACCARD_WEIGHT`, `MIN_JACCARD_BOOST`; add `_get_neighbor_set()`, `_jaccard_similarity()`; modify `retrieve_candidates()` to check topological overlap for sub-threshold candidates |
| `tests/test_topological_overlap.py` | NEW: test Jaccard computation, boost logic, threshold behavior, edge cases |

## API/Schema/Test Updates

- No tool catalog changes
- No schema changes
- No adapter changes
- Internal change to Step 5 retrieval only

## Validation Commands

```bash
python3 -m pytest tests/test_topological_overlap.py -v
python3 -m pytest tests/test_b111_sidequests_ledger.py -q  # regression check
```

## Risks / Constraints

- **False promotions**: High Jaccard overlap doesn't guarantee same entity. Two distinct concepts in the same domain may share many neighbors (e.g., "MySQL" and "PostgreSQL" both REQUIRE "SQL" and CO_OCCUR with "database"). Mitigation: the boost only promotes candidates into the gray zone — Step 6 arbitration makes the final decision.
- **Cold graph**: On a fresh graph with few edges, topological overlap will be zero for most candidates. This is fine — the feature has no effect and the existing vector-only path runs unchanged.
- **Query cost**: If the graph is very large (10K+ concepts), the neighbor set queries could slow down. Mitigation: limit neighbor set to 50 entries, which is sufficient for Jaccard estimation.

## Done When

- Sub-threshold candidates (0.55-0.75) checked for topological overlap
- High Jaccard overlap (>0.5) promotes candidates into gray zone
- Existing above-threshold behavior unchanged
- Performance stays under 50ms per candidate
- All tests pass
