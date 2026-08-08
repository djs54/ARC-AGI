# Plan: A185 — Don't Let Family-Level Falsification Evidence Poison Untested Click Targets

## Card metadata

- ID: A185
- Priority: P1
- Layer: ARC runtime
- Dependencies: A182

## Summary

`plan_generator.py::_build_candidates` computes `graph_score` (including the falsification-contradiction penalty) once per `action_id` and reuses it as the base score for every `target_variant`/`book_id` under that action, including `ACTION6`'s distinct click-target coordinates. A never-before-clicked coordinate inherits the entire family's aggregate contradiction history as its own starting penalty. Confirmed live: three genuinely untested coordinates scored `-4.58` each, worse than any actually-falsified action, while their own rationale text said `"untested"`.

## Technical approach

1. Read `agents/arc4/plan_generator.py::_build_candidates` in full (current version, post-A182) before editing — confirm the exact current structure of the `graph_score`/`graph_contradiction_penalty_applied` computation (lines ~151-223) and the per-`book_id` loop, since A182's edit already changed this method once tonight.
2. Split `graph_score`'s two components explicitly instead of one running float:
   - `graph_positive_score`: the `evidence_confidence` boost (line ~180-181) plus the A177 rule-confidence bonus (line ~198) — informative at the family level, safe to apply to any variant including untested ones.
   - `graph_contradiction_penalty`: the falsification-contradiction subtraction (currently folded into `graph_score -= ...`) — coordinate-specific in spirit, must only apply to a `book_id` that has itself actually been attempted.
3. In the per-`book_id` loop (`for book_id, payload, target_info in target_variants:`), change the `score = graph_score` starting line so that:
   - If `is_untested` (this specific `book_id`'s own `attempts == 0`): `score = graph_positive_score` (the contradiction penalty is withheld).
   - Else (this `book_id` has itself been attempted before): `score = graph_positive_score - graph_contradiction_penalty` (unchanged from current combined behavior — this preserves A182's fix for book_ids that have genuinely been tried).
4. `graph_contradiction_penalty_applied` (A182's flag, used later to gate the local `action_falsification_counts` penalty) should only be considered "applied" for a given `book_id` when the contradiction penalty was actually included in that `book_id`'s starting score — i.e., only in the "else" (tested) branch above. For an untested `book_id`, the local falsification penalty branch (`if falsifications and not graph_contradiction_penalty_applied`) is moot anyway since `falsifications` will be 0 for a genuinely untested `book_id` in the common case — but verify this reasoning against the actual code rather than assuming, since `action_falsification_counts` and `action_attempt_counts` are tracked by different keys/conditions and could theoretically disagree.
5. Do not change how `graph_score` (or its renamed components) is computed for non-`ACTION6` actions — those don't have per-`book_id` target variants (`book_id == action_id` always), so `is_untested` there already correctly reflects the family-level attempt count, and this bug doesn't apply to them. Confirm this is still true after the refactor (i.e., the fix should be a no-op for non-ACTION6 actions, not just "shouldn't matter" — prove it with a test).
6. Do not touch A177's rule-confidence bonus logic itself, `_voi_bonus`, or A184's `_apply_llm_patch` guard — this card is scoped to where the contradiction penalty is applied, nothing else.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/plan_generator.py` | `_build_candidates`: split `graph_score` into positive/contradiction-penalty components; apply the contradiction penalty only to `book_id`s that have themselves been attempted |
| `tests/test_a185_untested_click_targets_not_poisoned.py` (new) | Regression coverage (see Tests) |

## Tests

New `tests/test_a185_untested_click_targets_not_poisoned.py`:

1. This card's own reproduction: a `MockGraphPort`/stub returning heavy `contradictions` evidence for `action_id="ACTION6"` (e.g. `contradictions=9, supports=0`, matching tonight's live episode), combined with perception entities that produce fresh, never-attempted click targets (empty `attempted_coords`) — assert the resulting untested `ACTION6@x,y` candidates score at/near `untested_bonus`, not deeply negative, and specifically assert they do NOT score below `0.0`.
2. Positive-signal-still-transfers: same setup but with `evidence_confidence` or a live rule confidence present — assert an untested click-target's score reflects that positive contribution (not silently dropped along with the penalty).
3. Regression guard: a `book_id` that HAS itself been attempted and falsified (e.g. `ACTION6@16,38` tried twice before) is still penalized correctly — reuse A182's exact reproduction shape/numbers if practical, adapted to the click-target book_id case, to prove this card doesn't weaken that fix.
4. Non-ACTION6 regression guard: an ordinary action (e.g. `ACTION1`) with heavy family-level contradictions and `is_untested=True` (first attempt) — confirm behavior is unchanged from pre-A185 (i.e., prove this actually was never broken for non-click actions, don't just assume).

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a185_untested_click_targets_not_poisoned.py -v
make test-a
make test-all
```

Live confirmation: `campy status` health check, then `make smoke` (self-capped). Best-effort — depends on a run where `ACTION6` fails enough times to accumulate real contradiction evidence and then proposes a genuinely fresh coordinate afterward. If that exact combination doesn't occur, the unit tests (built directly from this card's live reproduction numbers) are the primary verification.

## Assumptions/defaults

- "Untested" is defined per-`book_id` (`state.action_attempt_counts.get(book_id, 0) == 0`), matching the existing `is_untested` variable already computed in the per-`book_id` loop — no new definition introduced.
- The split between "positive signal transfers, negative signal doesn't" is the chosen design (see card's Design section) — if investigation during implementation reveals a cleaner mechanism achieves the same effect, prefer it, but the behavioral outcome (untested coordinates not penalized by unrelated failures, positive signals still available) is the actual requirement.
