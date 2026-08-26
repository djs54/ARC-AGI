# Handoff: B278 `arc_update_goal_confidence` silently no-ops — `VictoryCondition` nodes are never created

**For:** hippocampy / Campy owner (B278 owns brain internals; ARC consumes across the MCP seam)
**From:** ARC_AGI A215 audit, Track A (2026-08-25)
**Status:** confirmed non-functional write path; not urgent (ARC-side local state already covers the practical gap — see "Why this isn't urgent" below), but worth fixing so the mechanism does what it appears to do

## Summary

`arc_update_goal_confidence` (`campy/brain/thalamus/tools/arc_queries.py:635-657`) is called unconditionally by ARC every cycle a goal is active (`agents/arc4/graph_queries.py::record_evaluation`), and `arc_get_goal_evidence` (`arc_queries.py:497-523`) reads the result back into ARC's goal-ranking logic. Both ends of this pipe are correctly wired — the bug is in the middle. **No code path anywhere in the current hippocampy codebase ever creates a `VictoryCondition` node.** Confirmed via exhaustive search (`grep -rn "VictoryCondition" campy/ --include="*.py"` across the whole `campy/` tree, and separately restricted to `MERGE`/`CREATE` statements) — every reference is either a `MATCH` (read or update-if-exists), a schema table declaration, or a provenance/tool-description string. `git log --all --oneline | grep -i victory` shows real `VictoryCondition` work happened historically (B172, B179), but that was on the archived v1 solver (`agents/arc3/solver.py`, now retired on the ARC_AGI side) — it never carried over to the current `arc_queries.py` tool surface v2 (`agents/arc4/*`) actually calls.

## The specific bug

```python
# arc_queries.py:642-655
current_result = db.execute(
    "MATCH (vc:VictoryCondition {condition_id: $gid}) RETURN vc.confidence",
    {"gid": goal_id},
)
current_row = _first_row(current_result)
current = _safe_float(_row_get(current_row, "vc.confidence", 0, 0.0))  # defaults to 0.0 if no row
...
await db.execute_write(
    "MATCH (vc:VictoryCondition {condition_id: $gid}) SET vc.confidence = $conf",
    {"gid": goal_id, "conf": gated_confidence},
)
return {"status": "ok", "goal_id": goal_id, "gated_confidence": gated_confidence}
```

Both queries are bare `MATCH`. If no `VictoryCondition` node exists for `condition_id = $gid` (which, per the search above, is every `goal_id` ARC ever sends, since nothing creates one), the read returns nothing (silently defaulted to `0.0`) and the write's `SET` matches zero rows and does nothing — but the function still returns `{"status": "ok", ...}`. ARC has no way to detect this from the response; it looks identical to a successful write. `arc_get_goal_evidence`'s own query (`MATCH (vc:VictoryCondition {task_id: $tid})`) correspondingly returns an empty `goals` list for every task, meaning `agents/arc4/goal_resolver.py`'s `_merge_graph_evidence` never receives a real `VictoryCondition`-sourced goal record to merge into any hypothesis.

## Why this isn't urgent

ARC-side (A215, Track B, verified against a real live-smoke trace from 2026-08-25) already demotes a goal that's making no progress purely from local state — `goal_resolver.py::_apply_failure_decay` multiplicatively decays a stuck goal's confidence (0.7^n per consecutive failure) within 2-3 cycles of `meaningful_progress=False`, independent of any graph signal. A real trace confirmed this firing correctly (goal `blob-2`: confidence 0.57 → 0.57 → 0.399 → 0.279 → 0.196 over 5 steps, then displaced by an alternative). So even once this write path is fixed, ARC's own `_merge_single_record`'s one-directional `confidence=max(hypothesis.confidence, boost)` merge (`goal_resolver.py:304` — a graph-sourced confidence can currently only push a hypothesis's score up, never down) is not currently causing any live harm, since the graph confidence it would apply is inert anyway. Fixing this handoff item would make the goal-confidence graph signal real, but ARC's own audit (A215) concluded no ARC-side code change is warranted right now regardless of whether this gets fixed.

## Ask (if/when worth prioritizing)

Give `arc_update_goal_confidence` a `MERGE` instead of `MATCH` for node existence, e.g.:

```cypher
MERGE (vc:VictoryCondition {condition_id: $gid})
ON CREATE SET vc.task_id = $tid, vc.confidence = $conf, vc.condition_type = $ctype
ON MATCH SET vc.confidence = $conf
```

(`task_id`/`condition_type` need to be threaded through as new params if not already present — check the current call signature in `agents/arc4/graph_queries.py::record_evaluation`, which currently sends `task_id`/`goal_id`/`new_confidence`/`has_meaningful_progress` but not `condition_type`; decide what a sensible default `condition_type` is for a goal ARC infers heuristically, since ARC doesn't currently have a formal taxonomy of victory-condition types to send.)

Also worth returning whether the write actually matched/created a node (e.g. `{"status": "ok", "created": bool, ...}`) so a future ARC-side check could distinguish a genuine no-op from a real write, instead of relying on this handoff doc as the only record.

## How ARC will know it's fixed

Run `--live-smoke`, then check `arc_get_goal_evidence`'s response for the active goal — it should return a non-empty `goals` list with a real `confidence` value tracking `meaningful_progress` across cycles, instead of an empty list. `agents/arc4/goal_resolver.py::_merge_graph_evidence`'s `graph_evidence` metadata field in the trace should show a real merged record for the active goal, not stay empty.
