# Plan: A176 — Persist Transitions as State Nodes

## Context

A170 computes `{row, col, from, to}` diffs and puts them in one LLM prompt, then discards them. A175 gives entities stable identity. This plan persists the diff, keyed by stable entities, as graph structure — with the explicit discipline that no schema lands without a tested consumer query (the pattern violated eight times over in A160-A175's findings).

## Step 0: Decide granularity (gate)

1. **Per-cell `Transition` nodes** (one node per changed cell per step) — maximally precise, but could grow large for actions that change many cells at once (e.g. a level-clear effect touching hundreds of cells).
2. **Per-effect summary nodes** (one node per action-effect, with an aggregate description — cell count, bounding region, color-transition histogram — plus a capped sample of individual cell changes) — bounded size, less precise for exact replay but sufficient for rule-signature extraction (A177).

Recommendation: (2). A177's rule-signature extraction needs shape/pattern, not perfect replay of every cell — a bounded summary is the right level of detail and avoids unbounded graph growth. Revisit only if A177 turns out to need per-cell precision the summary can't provide.

## Implementation

### Client side

`agents/arc4/graph_queries.py` gains a write method (e.g. `record_transition`) sending A170's diff (summarized per Step 0's decision) plus the acting `action_id`/step, keyed by A175's stable entity identifiers for any changed cell falling within a known entity's bounding box.

### Hand-off

`docs/handoff/B278-persist-transitions-as-state-nodes.md`: schema proposal (per Step 0's decision), and the specific new-tool ask (`arc_record_transition` write tool, `arc_get_entity_history` read tool).

### The consumer query (must land in the same card, not deferred)

Wire `arc_get_entity_history` (or equivalent) into `agents/arc4/plan_generator.py` or `goal_resolver.py` — even a minimal use (e.g. surfacing "this entity has changed N times before" as additional candidate-scoring context) satisfies the discipline requirement; a richer use can come later, but *some* real call site must exist before this card is considered done.

## Tests

New `tests/test_a176_transition_persistence.py`:

1. Client-side write payload correctness (stub `brain_client`, assert the diff is sent correctly summarized and keyed).
2. Consumer-query integration: stub graph port returning a canned transition history, assert it measurably affects planner/resolver output (mirroring A162/A171's pattern of proving a fix changes real behavior, not just that data flows).

## Verify

```bash
.venv/bin/python -m pytest tests/test_a176_transition_persistence.py -v
make test-a
make test-all
```

Live confirmation depends on the hand-off landing server-side; document what's client-verified now vs. pending.

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/graph_queries.py` | New transition-write method |
| `agents/arc4/plan_generator.py` or `goal_resolver.py` | Consumer query wired in |
| `docs/handoff/B278-persist-transitions-as-state-nodes.md` | New hand-off doc |
| `tests/test_a176_transition_persistence.py` | New tests |

## Risks

- Same cross-repo partial-completion risk as A175 — client-side work and the hand-off can land now; full live behavior depends on hippocampy implementing its half.
- Scope discipline: resist the temptation to also populate the broader unused `GridEntity` schema fields (`is_mobile`, `ADJACENT_TO`, etc.) while touching this area — stay scoped to transitions.
