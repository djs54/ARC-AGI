# Plan: A203 — Anchor-Biasing in `goal_resolver.py` / `plan_generator.py`

## Card metadata

- ID: A203
- Priority: P1
- Layer: ARC runtime
- Dependencies: A202

## Summary

Make `state.reasoner_anchor_hint` (produced by A202, currently unread) actually constrain `goal_resolver.resolve()` and `plan_generator.generate()`. No signature changes — both already receive `state` and can read the hint directly.

## Technical approach

### 1. `agents/arc4/goal_resolver.py`

Read `GoalResolver.resolve()` in full first (current structure, confirmed earlier this session: builds tier-1 hypotheses, merges tier-2 graph evidence, optionally escalates to tier-3 LLM, applies grounding gate, then `selected = hypotheses[0]`). Add, immediately before `selected = hypotheses[0]`:

```python
anchor_hint = getattr(state, "reasoner_anchor_hint", None)
if anchor_hint is not None and anchor_hint.anchor_type == "goal" and anchor_hint.decision in ("repeat_deepen", "repeat_retry"):
    anchored = next((h for h in hypotheses if h.goal_id == anchor_hint.anchor_ref), None)
    if anchored is not None and anchored is not hypotheses[0]:
        hypotheses = [anchored] + [h for h in hypotheses if h is not anchored]
```

This re-orders (doesn't discard alternatives) so the anchored hypothesis becomes `hypotheses[0]` only if it's still present among the resolved hypotheses; if it isn't found (`anchored is None`), resolution proceeds exactly as today, unconstrained. Confirm the exact point in the real function where hypotheses are finalized before `selected = hypotheses[0]` — there may be intervening steps (grounding gate, decay) this plan's sketch doesn't capture precisely; place the re-ordering after all of those, immediately before selection, not earlier where it could be undone by a later step.

### 2. `agents/arc4/plan_generator.py`

Read `_build_candidates()` in full first (confirmed earlier this session: builds `candidates: list[_CandidateRecord]`, already has A191's `continue` for `repeated_falsified` book_ids). Add, after the full `candidates` list is built (after the `state.latest_veto_alternative` re-add block, right before `return candidates`):

```python
anchor_hint = getattr(state, "reasoner_anchor_hint", None)
if anchor_hint is not None and anchor_hint.anchor_type == "entity":
    if anchor_hint.decision == "repeat_retry" and anchor_hint.required_book_id:
        target = next((c for c in candidates if c.book_id == anchor_hint.required_book_id), None)
        if target is not None:
            # Bypass scoring for this one candidate -- retry is a direct
            # instruction, not a scored preference. Do not resurrect it if
            # A191 already excluded it (target is None in that case, since
            # excluded book_ids never made it into `candidates` at all --
            # this is intentional: a genuinely falsified action must not be
            # retried just because the Reasoner asked to, the exclusion is
            # the stronger, correct signal).
            target.score = max(c.score for c in candidates) + 1.0
            target.rationale = f"{target.rationale}; reasoner requested retry"
    elif anchor_hint.decision == "repeat_deepen":
        for c in candidates:
            if c.metadata.get("entity_ref") == anchor_hint.anchor_ref:
                c.score += 0.3  # starting-point bias constant, no empirical basis yet -- same honest-gap pattern as A192's entity_neighborhood_weight
                c.metadata["reasoner_anchor_bias_applied"] = True
```

Note the `repeat_retry` branch's `target is None` case is deliberately a silent no-op (falls through to normal ranking) — this is the mechanism by which A191's exclusion wins over a retry request, and must not be "fixed" to force the retry through some other path.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/goal_resolver.py` | Anchor re-ordering before `selected = hypotheses[0]` |
| `agents/arc4/plan_generator.py` | Anchor biasing/retry-forcing before `_build_candidates` returns |
| `tests/test_a203_anchor_biasing.py` (new) | Coverage, see Tests |

## Tests

`tests/test_a203_anchor_biasing.py`:

1. `goal_resolver.resolve()` with `state.reasoner_anchor_hint=None` — identical output to a pre-this-card baseline (reuse an existing `goal_resolver` test fixture, assert unchanged).
2. `goal_resolver.resolve()` with a `goal`-type hint matching an existing hypothesis — that hypothesis is selected, regardless of its natural rank.
3. `goal_resolver.resolve()` with a `goal`-type hint that matches nothing in the resolved hypotheses — falls back to normal top-ranked selection, no crash.
4. `plan_generator._build_candidates()` with `state.reasoner_anchor_hint=None` — identical output to a pre-this-card baseline.
5. `plan_generator._build_candidates()` with a `repeat_retry` hint and a `required_book_id` matching a real candidate — that candidate has the highest score and an updated rationale.
6. `plan_generator._build_candidates()` with a `repeat_retry` hint whose `required_book_id` was excluded by A191 (falsified) — the candidate is *not* in the list, hint is a no-op, normal ranking proceeds (this is the test that locks in "A191 wins over a retry request").
7. `plan_generator._build_candidates()` with a `repeat_deepen` hint matching an entity_ref present among candidates — those candidates' scores increase and `reasoner_anchor_bias_applied` is set; candidates for other entities are unaffected.

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a203_anchor_biasing.py -v
.venv/bin/python -m pytest tests/test_arc4_goal_resolver.py tests/test_a192_entity_neighborhood_candidate_seeding.py tests/test_a191_prefilter_falsified_candidates.py -v
make test-a
make test-all
```

## Assumptions/defaults

- The `+0.3` deepen-bias constant and the "max score + 1.0" retry-force value are starting points with no empirical basis, same honest-gap treatment as every other new scoring constant introduced this session — document them as such in the Resolution, don't present them as tuned.
- This card must not weaken A191's exclusion under any circumstance — test 6 above is the non-negotiable regression guard for that. If implementation finds any path where an anchor hint could resurrect an excluded candidate, that's a bug to fix before this card can be considered complete, not an acceptable side effect.
