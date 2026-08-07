# Plan: A177 — Rules as Graph Nodes

## Context

Per-action counters (`falsified_count` etc.) can only answer "has this worked before." A176 gives persisted transitions; this plan extracts canonicalized causal claims (rules) from them, deterministically in code, and lets the LLM's role shrink to choosing/naming among structured candidates rather than extracting structure from raw text — directly addressing the concern that a small local model can't carry rule induction alone.

## Implementation

### 1. Candidate-rule-signature extraction (deterministic, this repo)

From a set of A176 transitions for a given action, derive a canonicalized signature: something like `(action_family, trigger_condition, effect_shape)` where `trigger_condition` might be "adjacent to color C" or "clicked cell itself" and `effect_shape` is a color-transition pattern (`X → Y`) with a spatial relation (radius, direction) — expressed as a comparable, hashable structure, not free text. Start with the simplest pattern family that covers observed cases (single-color-to-single-color transitions near the acted-on location) and extend only when live-smoke evidence shows a pattern class it can't represent.

### 2. Schema (hand-off)

`docs/handoff/B278-rules-as-nodes.md`: `Rule` node shape, `PREDICTS`/`FALSIFIED_BY`/`CONFIRMED_BY` edges referencing A176's `Transition` nodes, and the specific tool asks (`arc_record_rule`, `arc_get_rules_for_action`).

### 3. Scoring integration

`plan_generator.py::_build_candidates` gains a rule-evidence path alongside (not replacing, until proven better) the existing `falsification_penalty` computation — query live rules relevant to a candidate action, weight by their confirmed/falsified evidence.

## Tests

New `tests/test_a177_rules_as_nodes.py`:

1. Signature extraction correctness against known transition sequences — same underlying pattern with different colors produces matching signatures; different patterns don't collide.
2. Rule confirmation/falsification logic: a new transition matching a rule's prediction confirms it; a contradicting one falsifies it.
3. Scoring integration: a candidate backed by a live, unfalsified, high-confidence rule outranks an otherwise-identical candidate with no rule support.

## Verify

```bash
.venv/bin/python -m pytest tests/test_a177_rules_as_nodes.py -v
make test-a
make test-all
```

## Files Modified

| File | Change |
|------|--------|
| New module under `agents/arc4/` (e.g. `rule_extraction.py`) | Deterministic signature extraction |
| `agents/arc4/graph_queries.py` | Rule read/write methods |
| `agents/arc4/plan_generator.py` | Rule-evidence-aware scoring |
| `docs/handoff/B278-rules-as-nodes.md` | New hand-off doc |
| `tests/test_a177_rules_as_nodes.py` | New tests |

## Risks

- The largest design-risk card in this sequence — signature granularity (too coarse: rules never falsify anything meaningful; too fine: every transition is its own unique "rule," no generalization). Start narrow and expand deliberately, matching A175's Step 0 philosophy of not over-building ahead of evidence.
- Depends on A175 and A176 landing first (both client-side and their hand-offs) — do not start this card's implementation before those are in place, since rule evidence is meaningless without stable entities and persisted transitions to draw from.
