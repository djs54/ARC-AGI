# Handoff: B278 New tools needed — causal rules as graph nodes (PREDICTS/FALSIFIED_BY)

**For:** hippocampy / Campy owner (B278 owns brain internals; ARC consumes across the MCP seam)
**From:** ARC_AGI A177 strategic review (2026-08-07)
**Status:** ARC-side client (deterministic extraction, write call, consumer query) shipped; new server-side tools needed

## Summary

Every piece of "evidence" the graph currently tracks per action is a flat counter (`falsified_count`, `evidence_count`). These answer *"has this action worked before"* — a bandit-arm question. They cannot answer *"what does this action do, and under what conditions"* — the world-model question the architecture's own mission statement asks for. `Hypothesis` nodes exist but nothing populates them with an actual falsifiable prediction; they function as another counter wearing a different label.

## Division of labor (why this is tractable without a bigger model)

Extracting a *candidate rule signature* — "action=ACTION6 turns color 2 into color 5" — from an already-computed color-transition histogram (A176) is deterministic pattern-matching, not free-form reasoning. It's implemented client-side in `agents/arc4/rule_extraction.py`, fully unit-tested, no LLM involved. The server's job is purely bookkeeping: given a candidate signature, does it match an existing `Rule`'s prediction (confirm), contradict one (falsify), or represent something new?

## Ask: two new tools

### 1. `arc_record_rule` (write)

```json
{
  "task_id": "...",
  "step": 4,
  "action_id": "ACTION6",
  "candidate_signatures": [{"action_family": "ACTION6", "from_color": 2, "to_color": 5}]
}
```

For each signature: find existing `Rule` nodes for this `task_id` + `action_family` + `from_color`. If one has the same `to_color`, confirm it (bump confidence, add a `CONFIRMED_BY` edge to the underlying `Transition` node from A176, if useful). If one has a *different* `to_color`, falsify it (mark `falsified: true`, add `FALSIFIED_BY`). If none exists for this `action_family` + `from_color` combination, create a new `Rule` node with `PREDICTS` pointing at the observed effect shape.

### 2. `arc_get_rules_for_action` (read)

```json
{"task_id": "...", "action_id": "ACTION6"}
```

Returns:

```json
{"rules": [{"rule_id": "...", "from_color": 2, "to_color": 5, "confidence": 0.6, "falsified": false}]}
```

Live (unfalsified) rules relevant to this action — the direct consumer query.

## Suggested schema

```
Rule {rule_id, task_id, action_family, from_color, to_color, confidence, falsified, created_step}
PREDICTS (Rule -> GridEntity or effect-shape descriptor)
CONFIRMED_BY (Rule -> Transition)   -- A176's persisted Transition nodes
FALSIFIED_BY (Rule -> Transition)
```

Referencing A176's `Transition` nodes directly means a rule's evidence *is* a query over transitions, not a separately-maintained counter — consistent with the rest of this handoff sequence (A175 → A176 → this card) building one coherent structure rather than parallel bookkeeping systems.

## ARC-side status (no action needed from you on this half)

- `agents/arc4/rule_extraction.py` — pure, deterministic, fully tested: `extract_candidate_signatures` (transitions → candidate signatures) and `classify_signature` (signature vs. existing rules → confirms/falsifies/new). This client-side classification logic mirrors what the server-side tool should do — useful as a reference implementation, and could inform the server's own logic if useful, though the server is the authoritative source of truth once these tools exist.
- `agents/arc4/graph_queries.py::record_rule_evidence` — extracts signatures from A176's diff, calls `arc_record_rule`.
- `agents/arc4/graph_queries.py::fetch_rules_for_action` — calls `arc_get_rules_for_action`, degrades to `[]` on `capability_missing`.
- `agents/arc4/evaluator.py::_record_rule_evidence` — wired into the evaluate phase.
- `agents/arc4/plan_generator.py::_build_candidates` — **the consumer**: a candidate action's score is boosted by the confidence of its best live (unfalsified) rule, additive to the existing falsification-penalty mechanism (`rule_confidence_weight`, tunable).

Confirmed live that both new tools are currently absent (`capability_missing`/empty list) — the client degrades cleanly, not an error.

## How ARC will know it's fixed

Run `--live-smoke`, capture a step with a real color transition, confirm `arc_record_rule` no longer returns `capability_missing`. Then confirm a later plan-phase candidate for the same action family shows a nonzero rule-confidence contribution to its score.

Also unlocks A163 properly: once rules exist with real `FALSIFIED_BY`/`CONFIRMED_BY` edges, `arc_get_causal_path` (or a rule-aware equivalent) could return actual itemized supports/contradicts lists instead of the aggregate-only `path_confidence` signal A163's Branch A had to settle for — worth revisiting once this lands.
