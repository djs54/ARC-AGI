# Plan: A208 — Entity-Neighborhood Evidence: Score-Boost to Hard Admit/Exclude Gate

## Card metadata

- ID: A208
- Priority: P2
- Layer: ARC runtime
- Dependencies: A191, A192, A199

## Summary

Upgrade the entity-neighborhood evidence block in `plan_generator.py::_build_candidates` from purely additive scoring to a hard exclusion gate when the graph has actively falsified every hypothesis/rule about an entity — mirroring A191's book_id-level exclusion pattern at entity-neighborhood granularity.

## Technical approach

### 1. Read the current block in full first

Read `agents/arc4/plan_generator.py::_build_candidates`'s entity-neighborhood block (search for `entity_neighborhood_grounded`) and A191's `repeated_falsified` exclusion (`if repeated_falsified: continue`, earlier in the same function) side by side before changing anything — confirm current line numbers, since other cards may have shifted them since this plan was written.

### 2. Add the exclusion check

Immediately after the existing live-hypothesis/live-rule boost logic, before the block ends:

```python
entity_neighborhood_grounded = False
entity_ref = target_info.get("entity_ref")
if entity_ref is not None and graph_port is not None:
    fetch_neighborhood = getattr(graph_port, "fetch_entity_neighborhood", None)
    if fetch_neighborhood is not None:
        try:
            neighborhood = fetch_neighborhood(entity_ref)
            hypotheses = neighborhood.get("hypotheses", [])
            rules = neighborhood.get("rules", [])
            live_hypotheses = [h for h in hypotheses if not h.get("falsified")]
            live_rules = [r for r in rules if not r.get("falsified")]

            # A208: the graph has actively tested this entity and found
            # nothing that holds -- exclude the candidate entirely, the
            # same way A191 excludes a repeated_falsified book_id. Requires
            # the graph to have SOME record for this entity (hypotheses or
            # rules non-empty) that is uniformly falsified -- an entity
            # with no record at all (fresh, ungrounded) is NOT excluded,
            # since the graph hasn't said anything, positive or negative,
            # about it yet.
            entity_all_falsified = (
                (bool(hypotheses) and not live_hypotheses)
                or (bool(rules) and not live_rules)
            )
            if entity_all_falsified and not live_hypotheses and not live_rules:
                continue  # skip this target_variant entirely -- do not append a candidate

            if live_hypotheses:
                score += max(h.get("confidence", 0.0) for h in live_hypotheses) * self._limits.entity_neighborhood_weight
                entity_neighborhood_grounded = True
            if live_rules:
                score += max(r.get("confidence", 0.0) for r in live_rules) * self._limits.entity_rule_weight
                entity_neighborhood_grounded = True
        except Exception:
            pass
```

Read this sketch carefully against the real current code before applying — the exact `continue` target must skip only this `target_variant` (one ACTION6 coordinate), not the whole `action_id` loop, matching how A191's `continue` is scoped. Confirm this by checking what loop level the existing entity-neighborhood block sits inside (the per-`target_variant` `for book_id, payload, target_info in target_variants:` loop, per A192's original placement) before finalizing the `continue`'s scope — get this wrong and it could skip other coordinates/actions that shouldn't be affected.

**Correctness note on the exclusion condition:** `entity_all_falsified` as sketched above is redundant with the final `if` — simplify during implementation to exactly: exclude when (`hypotheses` is non-empty AND `live_hypotheses` is empty) OR (`rules` is non-empty AND `live_rules` is empty), AND both `live_hypotheses`/`live_rules` are empty (i.e., truly nothing live remains from either evidence type). Write this as a single clear boolean, not two overlapping checks — the sketch above has redundant logic, clean it up rather than copying verbatim.

### 3. Telemetry decision

Decide during implementation: does the exclusion case need a distinct telemetry flag (e.g., `entity_neighborhood_excluded: True` in a candidate's metadata, mirroring `repeated_falsified`), or is "this candidate simply doesn't appear in `candidates`" sufficient signal (matching how A191's exclusion has no separate telemetry flag either — its absence from the list *is* the signal, verified by A195's Shift-B invariant check)? Prefer consistency with A191's precedent (no separate flag) unless a concrete reason emerges during implementation to diverge — document whichever choice is made and why.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/plan_generator.py` | Entity-neighborhood block gains a hard-exclusion branch |
| `tests/test_a208_entity_neighborhood_hard_exclusion.py` (new) | Coverage, see Tests |

## Tests

`tests/test_a208_entity_neighborhood_hard_exclusion.py`:

1. Entity with `hypotheses=[{"falsified": True, ...}]` (only falsified, non-empty) — candidate for that entity is absent from `candidates`.
2. Entity with `rules=[{"falsified": True, ...}]` (only falsified, non-empty) — same exclusion.
3. Entity with a mix — `hypotheses=[{"falsified": True}, {"falsified": False, "confidence": 0.7}]` — candidate is present AND scored with the live hypothesis's boost (not excluded; the existing positive-boost path still fires).
4. Entity with `hypotheses=[]` and `rules=[]` (no record at all) — candidate present, unaffected, no boost applied (matches current behavior for an ungrounded entity).
5. Orthogonality with A191: a fresh `book_id` (zero local attempts, `repeated_falsified=False`) whose entity IS neighborhood-excluded — confirm the candidate is excluded via the NEW check even though A191's own check would not have excluded it, proving the two mechanisms are independent, not one gating the other.
6. `fetch_entity_neighborhood` raising an exception — falls back to the pre-existing `except Exception: pass` behavior, candidate is NOT excluded (exclusion requires actually observing falsified evidence, not a failed fetch).
7. A184's LLM-patch guard and A188's vetter veto tests (existing suites) — confirm unaffected, still pass unmodified.

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a208_entity_neighborhood_hard_exclusion.py -v
.venv/bin/python -m pytest tests/test_a191_prefilter_falsified_candidates.py tests/test_a192_entity_neighborhood_candidate_seeding.py tests/test_b359_entity_rule_wiring.py -v
make test-a
make test-all
```

## Assumptions/defaults

- This card does not change A191's book_id-level exclusion in any way — the two mechanisms operate on different keys (book_id vs. entity_ref) and must remain independently checkable, per acceptance criterion 5.
- If `fetch_entity_neighborhood` is unavailable (capability_missing / older graph_port), behavior is unchanged from today — no exclusion, same as any other capability-degraded path this session has established.
