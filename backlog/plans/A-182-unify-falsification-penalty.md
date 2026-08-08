# Plan: A182 — Unify the Duplicated Falsification Penalty in Plan Scoring

## Card metadata

- ID: A182
- Priority: P3
- Layer: ARC runtime
- Dependencies: A177, A178

## Summary

`agents/arc4/plan_generator.py::_build_candidates` applies a falsification penalty from two sources that track the identical event within a single continuous episode: the graph's persistent `ActionFact`-derived `contradictions` count (subject to `repeat_decay_factor`, since it lives inside `graph_score`) and the local `state.action_falsification_counts` (applied flat, never decayed). Confirmed live across three separate `make smoke` runs (2026-08-08) by reverse-engineering real candidate scores to exact matches — the duplication is 100% reproducible whenever an action has been falsified at least once, not intermittent.

Decision (see `backlog/A182.md`): unify so the graph's signal is authoritative when it has real evidence; the local counter is a fallback only, used when the graph has nothing to say (unavailable, or genuinely no evidence yet — these two cases are indistinguishable from `fetch_per_action_evidence`'s existing return shape, which is not itself in scope to change here).

## Technical approach

1. In `agents/arc4/plan_generator.py::_build_candidates` (around lines 158-183), track whether the graph-based contradiction penalty was actually applied for this `action_id`:

```python
graph_contradiction_penalty_applied = False
if graph_port is not None:
    try:
        graph_evidence = graph_port.fetch_per_action_evidence(action_id)
        evidence_confidence = graph_evidence.get("confidence", 0.0)
        evidence_contradictions = graph_evidence.get("contradictions", 0)
        evidence_supports = graph_evidence.get("supports", 0)
        if evidence_confidence > graph_score:
            graph_score = evidence_confidence
        if evidence_contradictions > evidence_supports:
            graph_score -= self._limits.falsification_penalty * (evidence_contradictions - evidence_supports)
            graph_contradiction_penalty_applied = True
    except Exception:
        pass
    ...
```

2. Where the local penalty is applied (around line 219-220, inside the per-`book_id` loop — note `graph_contradiction_penalty_applied` is computed once per `action_id`, outside that loop, and must be read inside it unchanged for every `book_id`/target variant of that same action):

```python
if falsifications and not graph_contradiction_penalty_applied:
    score -= min(self._limits.falsification_penalty * falsifications, 0.55)
```

3. Do not change `fetch_per_action_evidence` itself (`agents/arc4/graph_queries.py`) — its `capability_missing`-degrades-to-zero-evidence behavior is a pre-existing, separate property this card relies on for the fallback case, not something to fix here.
4. Do not change the rule-confidence bonus logic (lines 171-183) — this card is scoped to the falsification *penalty* duplication only, not the rule bonus, which already behaves correctly (confirmed: it's the intermittent signal, not the duplicated one).

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/plan_generator.py` | Track `graph_contradiction_penalty_applied`; gate the local falsification penalty on it being `False` |
| `tests/test_a182_unified_falsification_penalty.py` (new) | Regression coverage (see Tests) |

## Tests

New `tests/test_a182_unified_falsification_penalty.py`:

1. Graph reports a real contradiction (`contradictions=2, supports=0`) for an action that also has `state.action_falsification_counts[action] = 2` — assert the resulting score matches a *single*, decayed graph-based penalty, not the old double-counted value. Use this card's own reproduction numbers as the regression fixture (recompute what the *old* formula would have given for the same inputs and assert the new score is less negative / higher than that).
2. Graph reports no evidence (`fetch_per_action_evidence` returns the `capability_missing`-shaped zero dict) but local state has `action_falsification_counts[action] = 2` — assert the local flat penalty still applies (score reflects it, not zero).
3. `graph_port=None` entirely, local state has falsifications — same fallback assertion as (2).
4. `fetch_per_action_evidence` raises — same fallback assertion as (2) (the existing `try/except` around it must still leave `graph_contradiction_penalty_applied=False` in this case).
5. Regression guard: an action with zero falsifications and zero contradictions is unaffected (score unchanged from current behavior) — this card must not touch the untested-action or first-attempt scoring paths at all.

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a182_unified_falsification_penalty.py -v
make test-a
make test-all
```

Live confirmation: `campy status` health check, then `make smoke` (self-capped per this session's methodology). If a repeated-falsification case occurs in the run, reverse-engineer its score by hand (same technique used to find this bug) and confirm it now matches the single-penalty formula. If no repeated-falsification case occurs in that particular run (not fully controllable), the unit tests are the primary verification — note this honestly rather than blocking on a specific run outcome.

## Assumptions/defaults

- The "graph is authoritative once it has real evidence" rule uses `evidence_contradictions > evidence_supports` as the signal of "the graph has something to say" — same condition the existing code already uses to decide whether to apply the graph penalty at all, so this introduces no new threshold or magic number.
- Does not attempt to fix the underlying ambiguity in `fetch_per_action_evidence` between "genuinely no evidence" and "capability_missing" — both correctly fall through to the local-counter fallback under this design, which is safe (no signal loss) even though it can't distinguish the two cases.
