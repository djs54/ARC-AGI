# Handoff: B278 New tool needed — cross-game rule transfer by structural fingerprint

**For:** hippocampy / Campy owner (B278 owns brain internals; ARC consumes across the MCP seam)
**From:** ARC_AGI A179 strategic review (2026-08-07)
**Status:** ARC-side client (fingerprinting, write enrichment, consumer query) shipped; new server-side tool needed

## Summary

The only cross-game retrieval mechanism today is `action_signature` (`campy/brain/thalamus/tools/arc_mechanics.py`) — a hash of which action buttons a game exposes. Live evidence: two distinct mechanic records with identical `action_signature` values both carried the description *"The game follows a space archetype."* This is nearly meaningless as a similarity key — "has ACTION1-5" is true of a large fraction of ARC-AGI-3 games regardless of what those actions actually *do*. Real transfer needs to key on causal claims (A177's rules), not surface features.

## The fingerprint

`action_family` (ACTION1..ACTION7) is already game-invariant — fixed by the ARC-AGI-3 action vocabulary, not a per-game choice. Literal colors are the opposite: "color 3" means nothing across two games with different palettes. So the fingerprint (`agents/arc4/rule_extraction.py::compute_fingerprint`) keeps `action_family` and replaces colors with a **magnitude bucket** — how many cells a transition touched, bucketed into `single` (1 cell) / `small` (2-4) / `medium` (5-19) / `large` (20+). Color-invariant, but still captures real shape difference: "this action toggles one cell" vs. "this action recolors a whole region" are genuinely different mechanics, and that distinction *does* transfer across palettes.

Fingerprint key format: `"{action_family}:{magnitude}"` (e.g. `"ACTION6:small"`).

## Ask: one new tool

### `arc_get_transferred_rules` (read)

```json
{"task_id": "current-task-id", "fingerprint": "ACTION6:small"}
```

Returns rules from **other** `task_id`/`game_id`s (not the current one — this is specifically cross-game; A164's existing per-game scoping already handles in-game evidence via `arc_get_rules_for_action`) whose recorded fingerprint matches:

```json
{"rules": [{"rule_id": "...", "confidence": 0.6, "source_game_id": "..."}]}
```

Suggested implementation: index `Rule` nodes (from A177's handoff) by the fingerprint recorded alongside each `arc_record_rule` call (see below — the client now sends `fingerprint` in that payload), and query across all `task_id`s except the caller's own.

## ARC-side status (no action needed from you on this half)

- `agents/arc4/rule_extraction.py::compute_fingerprint`/`magnitude_class`/`StructuralFingerprint` — pure, tested (same-shape-different-colors produces the same fingerprint; genuinely different magnitudes don't collide).
- `agents/arc4/graph_queries.py::record_rule_evidence` — now includes `"fingerprint": "..."` in its payload (the write side already sends what's needed to index by fingerprint once you're ready).
- `agents/arc4/graph_queries.py::fetch_transferred_rules` — calls `arc_get_transferred_rules`, degrades to `[]` on `capability_missing`.
- `agents/arc4/goal_resolver.py::_tier_one_hypotheses` — **the consumer**: when a candidate goal's entity has real in-game transition history (A176), fingerprints its most recent observed transition and checks for cross-game matches, applying a confidence boost **explicitly smaller** than the in-game-evidence boost (`0.05` weight vs. `0.08` flat) — a transfer is a lead, not a fact, and must not be trusted as much as a confirmed-in-this-game observation.

Confirmed live that the new tool is currently absent (`capability_missing`) — the client degrades cleanly, not an error.

## How ARC will know it's fixed

Run `--live-smoke` across two different games in the same session (or two separate runs landing in the same manifest slot). Confirm `arc_get_transferred_rules` no longer returns `capability_missing`, and that a rule learned in the first game surfaces (via `"entity_history:transfer_match"` in a hypothesis's evidence tuple) when planning for the second, structurally-similar one.
