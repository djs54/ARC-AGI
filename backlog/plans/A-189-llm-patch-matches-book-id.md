# Plan: A189 — Scope One LLM Patch to a Single Candidate, Not a Whole Action Family

## Card metadata

- ID: A189
- Priority: P1
- Layer: ARC runtime
- Dependencies: A184, A185

## Summary

`plan_generator.py::_apply_llm_patch` matches `patch["action_id"]` against every candidate's `action_id`. For non-click actions this matches at most one candidate; for `ACTION6` it can match several distinct `book_id` coordinates sharing the same family, boosting every one of them that isn't individually `repeated_falsified`. Combined with A185's diversity mechanism (which keeps minting fresh untested coordinates), a single LLM escalation naming `"ACTION6"` can repeatedly out-score `ACTION1`-`ACTION5`'s flat untested score, letting `ACTION6` entrench for an entire episode. Confirmed live in `backlog/A189.md` with a direct reproduction (3 distinct `ACTION6@x,y` candidates all matched a single patch).

## Technical approach

1. Read `agents/arc4/plan_generator.py::_apply_llm_patch` and `_parse_llm_response` in full before editing — confirm current line numbers (both have been touched by A184 already tonight).
2. Change the matching loop so at most one candidate per LLM patch is eligible for the score boost — the highest-scoring candidate among those sharing `action_id == patch["action_id"]`:
   ```python
   action_id = str(patch.get("action_id"))
   same_family = [c for c in candidates if c.action_id == action_id]
   target = max(same_family, key=lambda c: c.score, default=None)
   updated = []
   for candidate in candidates:
       if target is not None and candidate is target:
           # existing A184 repeated_falsified guard + boost logic, applied only to `target`
           ...
       else:
           updated.append(candidate)
   ```
3. Preserve A184's `repeated_falsified` guard exactly as-is, now applied only to `target`.
4. Preserve the `if not matched:` fallback branch (LLM suggests an action_id not present in any candidate at all) unchanged — that path already constructs a single new candidate, no fan-out risk there.
5. Check `_parse_llm_response`'s regex-matching against the raw LLM response text (`seen_ids`/`best_id` logic, lines ~525-541) — confirm it already returns a bare family id (not a coordinate), consistent with what the escalation prompt actually asks for. If it's structurally impossible for the LLM to name a specific `book_id`, don't add unused coordinate-parsing machinery — best-scoring-single-candidate is the correct, sufficient fix.
6. Do not touch `_should_force_explore` or any of A185's diversity-mechanism code — this card only changes how many candidates one patch is allowed to touch.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/plan_generator.py` | `_apply_llm_patch`: select a single best-scoring same-family `target` candidate for the boost, leave all other same-family candidates unmodified |
| `tests/test_a189_llm_patch_scoped_to_one_candidate.py` (new) | Regression coverage (see Tests) |

## Tests

New `tests/test_a189_llm_patch_scoped_to_one_candidate.py`:

1. This card's exact live reproduction: 3 distinct `ACTION6@x,y` candidates (mix of untested and `repeated_falsified`), patch names `"ACTION6"` — assert only one candidate is boosted/tagged `llm_guidance`, all others pass through with unchanged score and metadata.
2. The boosted candidate is the highest-scoring among the same-family candidates (not an arbitrary one) — construct a case where the untested coordinate and a not-yet-falsified-but-lower-scoring coordinate are both eligible, assert the higher-scoring one wins.
3. A184's `repeated_falsified` guard still applies to whichever candidate is selected as `target` — reuse/adapt A184's existing exact-reproduction test, confirm it still passes unmodified against the new matching logic.
4. Regression guard: non-click actions (`book_id == action_id`, at most one candidate can ever match) behave identically before and after this change.
5. Regression guard: the `not matched` fallback path (LLM names an action_id absent from all candidates) is unaffected.

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a189_llm_patch_scoped_to_one_candidate.py -v
make test-a
make test-all
```

Live confirmation: `campy status` health check, then a smoke run with an increased step budget (best-effort — depends on an LLM escalation firing while multiple `ACTION6` coordinates are simultaneously present in the candidate list, which needs both enough steps for A185's diversity mechanism to generate multiple fresh coordinates and a low enough top score to trigger the `llm_low_score_threshold` escalation).

## Assumptions/defaults

- Best-scoring-single-candidate is the chosen resolution strategy, not attempting to parse a `book_id`-shaped coordinate out of the LLM's free-text response — the escalation prompt asks for a bare `action_id`, and there's no existing evidence the LLM ever names a specific coordinate. If implementation reveals otherwise, revisit before committing to this approach.
- This card is scoped to `plan_generator.py::_apply_llm_patch` only. A188 (found the same session) covers the complementary `plan_vetter.py` book_id/action_id key-mismatch issue — the two are independently fixable and independently testable, though both stem from the same book_id/action_id granularity distinction established by A185.
