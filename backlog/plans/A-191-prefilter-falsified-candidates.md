# Plan: A191 — Exclude Repeatedly-Falsified `book_id`s From the Candidate Set

## Card metadata

- ID: A191
- Priority: P2
- Layer: ARC runtime
- Dependencies: A184, A185, A187, A188

## Summary

`plan_generator.py::_build_candidates` computes `repeated_falsified = falsifications >= 2` per book_id (`plan_generator.py:217`) but only uses it to penalize score and annotate rationale. Change it to skip candidate construction entirely for a `repeated_falsified` book_id, so a known-dead option never reaches scoring, the LLM escalation prompt, or the vetter — those remain as defense-in-depth, not the primary filter.

## Technical approach

1. Read `agents/arc4/plan_generator.py::_build_candidates` in full (lines 124-327) before editing — confirm current line numbers.
2. Immediately after `repeated_falsified = falsifications >= 2` (currently line 217), add the skip:
   ```python
   is_untested = attempts == 0
   repeated_falsified = falsifications >= 2
   if repeated_falsified:
       continue
   ```
   Placed before any of the score/rationale computation for that `book_id, payload, target_info` triple in the `for book_id, payload, target_info in target_variants:` loop — the candidate is simply never built, not built-then-discarded.
3. Confirm this `continue` is scoped to the inner `target_variants` loop (per-book_id), not the outer `for action_id in available_actions` loop — a single `ACTION6` with 3 click targets where only 1 is repeatedly falsified must still produce candidates for the other 2.
4. Do not touch `graph_contradiction_penalty`, `graph_positive_score`, `_voi_bonus`, or any other scoring logic computed earlier in the outer loop (those still apply to every non-excluded candidate exactly as before).
5. Confirm `_fallback_candidate` (lines 304-305, `if not candidates:`) still fires correctly in the pathological case where every `target_variants` entry across every `action_id` is repeatedly falsified — this should require no code change (the existing check already covers "candidates ended up empty" regardless of why), but must be covered by a test.
6. Do not modify the `state.latest_veto_alternative` re-add block (lines 307-325) — it runs after the main loop and is unconditional on `repeated_falsified`, which is correct and intentional (it's the vetter's own suggested replacement, a different mechanism). Add a test confirming it still fires even when the veto alternative's own book_id happens to be repeatedly falsified (this is existing behavior, not a change — the test documents and locks it in).
7. Do not touch `_apply_llm_patch` (A184/A189's guards) or `plan_vetter.py` (A188's veto) — both stay exactly as-is. They should simply stop firing in practice for excluded candidates (nothing left for them to catch), which the new tests should confirm rather than assume.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/plan_generator.py` | `_build_candidates`: `continue` immediately after computing `repeated_falsified = True` for a book_id, before any candidate construction |
| `tests/test_a191_prefilter_falsified_candidates.py` (new) | Coverage (see Tests) |

## Tests

New `tests/test_a191_prefilter_falsified_candidates.py`:

1. A non-click action (e.g. `ACTION1`) with `action_falsification_counts["ACTION1"] = 2` produces zero candidates for `ACTION1` in `_build_candidates`'s output.
2. A non-click action with `falsifications = 1` (below threshold) is unaffected — candidate is built, scored, and rationale-annotated exactly as before this change (regression guard).
3. `ACTION6` with 3 click targets where exactly one book_id (`ACTION6@x,y`) has `falsifications = 2` and the other two have `0`: exactly 2 `ACTION6` candidates are produced (the falsified coordinate excluded, the other two present and correctly scored).
4. Pathological case: every available action/book_id is repeatedly falsified — `_build_candidates` returns exactly one candidate, the fallback probe from `_fallback_candidate`, not an empty list.
5. `state.latest_veto_alternative` re-add still appends its candidate even when that alternative's own book_id is repeatedly falsified (documents the intentional exception to this card's filter).
6. End-to-end: reuse/adapt A184's and A188's exact live-reproduction scenarios (the repeatedly-falsified `ACTION6@18,17`/etc. book_ids from those cards) and confirm the repeatedly-falsified coordinates never appear in `_build_candidates`'s output at all now — their existing "guard fires correctly" assertions in `test_a184_*.py`/`test_a188_*.py` should still pass unchanged (there's simply nothing left for the guards to need to catch in this exact scenario, but they must remain correct for cases where a candidate becomes falsified mid-episode after already being selected once).

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a191_prefilter_falsified_candidates.py -v
.venv/bin/python -m pytest tests/test_a184_llm_patch_respects_falsification_verdict.py tests/test_a188_vetter_keys_by_book_id.py tests/test_a189_llm_patch_scoped_to_one_candidate.py -v
make test-a
make test-all
```

Live confirmation: `make smoke` — best-effort per the A182-A189 caveat (requires a run that accumulates real per-book_id falsification history above threshold within the run's step budget).

## Assumptions/defaults

- This card changes *when* a repeatedly-falsified candidate stops being considered (at construction instead of at selection/patch time), not the falsification threshold or accounting itself — `falsifications >= 2` is the same condition already computed today, reused as-is, not reconfigured.
- A184's and A188's guards are intentionally left in place, not removed, even though this card should make them unreachable for the specific case of "a candidate that was already repeatedly-falsified before this planning pass began." They still matter for the case where a candidate crosses the threshold *during* the current planning pass's own evaluation (e.g. becomes falsified from this step's own outcome, after `_build_candidates` already ran) — that timing case is out of scope for this card and must not regress.
