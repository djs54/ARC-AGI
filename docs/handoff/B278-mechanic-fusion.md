# Handoff: B278 New tools needed — aggregate Mechanic records fused from cross-game rule transfer

**For:** hippocampy / Campy owner (B278 owns brain internals; ARC consumes across the MCP seam)
**From:** ARC_AGI A186 (2026-08-17), layered on A179's structural-signature transfer
**Status:** ARC-side client (blocking, structure-layer matching, deterministic merge, write/read enrichment, consumer) shipped; new server-side tools needed. Client currently degrades cleanly — see "Why nothing fuses yet" below.

## Summary

A179 retrieves individual rules from other games by structural fingerprint (`action_family` + magnitude bucket), one at a time. A179's own review already found the fingerprint alone is a weak similarity signal — two unrelated mechanics can share a fingerprint by coincidence ("same buttons exist" was shown to describe two unrelated games identically). A186 adds a second, independent check before trusting a fingerprint match enough to treat multiple transferred rules as one reusable **Mechanic**: do the rules that share a fingerprint also share the state they fired on (their *preconditions*)?

This is the graph-engineering fusion pipeline applied to A179's output: **block** transferred rules by fingerprint (reusing A179's key, not a new one), **match** within a block by shared precondition features, and **merge** confident matches into one Mechanic record via deterministic code (no LLM call). Ambiguous or no-match pairs are never force-merged — an incorrect merge would silently combine two unrelated mechanics' entire evidence trails, which the graph-engineering material calls out as strictly worse than leaving them unfused.

## Preconditions: what they are and why they exclude color

A "precondition" here is a small, deterministic, palette-invariant feature-tag list describing the entity a rule fired on, computed by `agents/arc4/rule_extraction.py::entity_preconditions`:

- `kind:{entity.kind}` — the perceived entity type (e.g. `blob`, `point`)
- `size_class:{magnitude_class(cell_count)}` — the same `single`/`small`/`medium`/`large` bucketing A179 already uses for transition magnitude, applied to entity size
- `shape_class:{tall|wide|square}` — bounding-box aspect ratio only

Literal color is deliberately excluded, for the same reason A179's fingerprint excludes it: "color 3" carries no meaning across two games with different palettes, but "small, roughly square blob" does. Two transferred rules are considered a **confident match** when they share **3 or more** precondition features (`agents/arc4/mechanic_fusion.py::CONFIDENT_MATCH_MIN_SHARED_PRECONDITIONS`) — chosen to match the graph-engineering fusion material's own worked example ("two nodes sharing 3 coauthors and an affiliation are the same entity").

## Why nothing fuses yet (this is expected, not a bug)

`get_transferred_rules` today returns only `rule_id`, `confidence`, `source_game_id` per rule — no precondition data. The ARC-side client now sends `preconditions` on every `record_rule` write (see below) and defensively reads a `preconditions` field back from `get_transferred_rules` if present, defaulting to `()` when it's not. An empty tuple can never reach the 3-feature match threshold, so **today, in production, A186's fusion pass will correctly find zero confident matches** — a clean degrade, not a broken feature. It becomes real the moment the two asks below land.

## Ask: two things

### 1. Store `preconditions` on `Rule` nodes, echo it back from `get_transferred_rules`

`record_rule`'s payload (via `agents/arc4/graph_queries.py::record_rule_evidence`) now includes:

```json
{"preconditions": ["kind:blob", "size_class:small", "shape_class:square"]}
```

Store this array alongside the `Rule` node (same node A177's handoff already asked for). `get_transferred_rules`'s response should echo it back per rule:

```json
{"rules": [{"rule_id": "...", "confidence": 0.6, "source_game_id": "...", "preconditions": ["kind:blob", "size_class:small", "shape_class:square"]}]}
```

No new tool needed for this half — just two new fields on the existing A177/A179 tools.

### 2. Two new tools for the fused `Mechanic` record itself

**`record_mechanic` (write)**

```json
{
  "task_id": "current-task-id",
  "fingerprint": "ACTION6:small",
  "member_rule_ids": ["rule-A", "rule-B"],
  "source_game_ids": ["game-A", "game-B"],
  "confidence": 0.55,
  "merged_from": ["rule-A", "rule-B"]
}
```

The merge (which rules to fuse, and the aggregate confidence — always kept strictly below the strongest member's own confidence) is already computed client-side (`agents/arc4/mechanic_fusion.py::merge_confident_candidates`); this call is a pure write of that result. Suggested implementation: upsert a `Mechanic` node keyed by `fingerprint`, with edges to each member `Rule` node (union `member_rule_ids` into the existing set on repeat calls rather than overwriting, since the same fingerprint may accumulate more corroborating rules over time).

**`get_mechanic_candidates` (read)**

```json
{"task_id": "current-task-id", "fingerprint": "ACTION6:small"}
```

```json
{"mechanics": [{"mechanic_id": "...", "confidence": 0.55, "member_rule_ids": ["rule-A", "rule-B"]}]}
```

Not yet wired into a consumer (out of scope for this card — reserved for a future `GameArchetype`/`FailureMode`/`RecoveryPolicy` card that reads fused Mechanics rather than recomputing fusion from raw transferred rules each time).

## ARC-side status (no action needed from you on this half)

- `agents/arc4/rule_extraction.py::shape_class`/`entity_preconditions` — pure, tested.
- `agents/arc4/mechanic_fusion.py` — `block_by_fingerprint`, `match_within_block`, `merge_confident_candidates`, `fuse_transferred_rules` — pure, tested, no LLM call, conservative (ambiguous pairs never force-merged).
- `agents/arc4/graph_queries.py::record_rule_evidence` — now includes `"preconditions": [...]` in its payload; `fetch_transferred_rules` now reads a `preconditions` field back (defaults to `()`), and a `fingerprint` field is filled in client-side from the query key.
- `agents/arc4/graph_queries.py::record_mechanic_fusion`/`fetch_mechanic_candidates` — call `record_mechanic`/`get_mechanic_candidates`, degrade to a clean no-op/`[]` on `capability_missing`.
- `agents/arc4/goal_resolver.py::_tier_one_hypotheses` — **the consumer**: after A179's single-rule transfer boost, re-fuses the already-fetched transferred-rule list (no extra network call) and applies a second, smaller confidence boost when a confident Mechanic fusion exists (`0.025` weight vs. A179's `0.05` — see `_MECHANIC_FUSION_CONFIDENCE_MULTIPLIER` in `goal_resolver.py`), tagged `"entity_history:mechanic_fusion"`. Opportunistically calls `record_mechanic_fusion` when the graph port supports it.

Confirmed via unit tests that the pipeline behaves correctly on both the degraded-empty-preconditions shape (today's real server behavior) and a synthetic non-empty-preconditions shape (the target behavior once this hand-off lands).

## How ARC will know it's fixed

Once `preconditions` is stored/echoed and `record_mechanic`/`get_mechanic_candidates` exist: run `--live-smoke` across two structurally-similar games in one session (or two runs landing in the same manifest slot, matching A179's own verification approach). Confirm `record_mechanic` stops returning `capability_missing`, and that `"entity_history:mechanic_fusion"` appears in a hypothesis's evidence tuple when the same fingerprint accumulates 2+ transferred rules that share 3+ precondition features.
