# Plan: A188 — Key `plan_vetter.py`'s Lookups by `book_id`, Not `action_id`

## Card metadata

- ID: A188
- Priority: P1
- Layer: ARC runtime
- Dependencies: A185, A187

## Summary

`plan_vetter.py::vet()` and `_choose_alternative()` look up `state.action_attempt_counts`/`action_falsification_counts` by `candidate.action_id` / `alternative.action_id`. Those dicts are keyed by `book_id` (per `workflow.py::_record_execution_attempt`), which only equals `action_id` for non-click actions. For `ACTION6`, the lookup always misses and silently defaults to 0, permanently disabling the `repeated_falsification_threshold` and `excessive_repetition_threshold` vetoes for click-target candidates, and making `_choose_alternative` treat any `ACTION6` coordinate as forever "untested." Confirmed live with a direct reproduction in `backlog/A188.md` (19 real attempts / 13 real falsifications on `ACTION6`, `vet()` reports 0/0).

## Technical approach

1. Read `agents/arc4/plan_vetter.py` in full before editing — confirm current line numbers for `vet()` and `_choose_alternative()`.
2. Add a small helper (or inline resolution) that mirrors `workflow.py::_record_execution_attempt`'s own key resolution:
   ```python
   def _book_id(candidate: PlanCandidate) -> str:
       return str(candidate.metadata.get("book_id") or candidate.action_id)
   ```
3. In `vet()`, replace the two lookups:
   ```python
   book_id = _book_id(candidate)
   candidate_falsifications = int(state.action_falsification_counts.get(book_id, 0))
   candidate_attempts = int(state.action_attempt_counts.get(book_id, 0))
   ```
4. In `_choose_alternative()`, replace `int(state.action_attempt_counts.get(alternative.action_id, 0)) == 0` with the same `_book_id(alternative)`-keyed lookup, for both the `alternatives` loop and the `state.latest_veto_alternative` fallback.
5. Do not touch `_check_graph_gate` (keyed by `action_id` deliberately — the graph gate is a family-level check, separate concern) or `_weak_evidence_warning` (uses the already-corrected `falsifications` value passed in, no independent lookup).
6. Confirm `PlanCandidate.metadata["book_id"]` is reliably populated for every candidate reaching the vetter — check `plan_generator.py`'s candidate-construction sites (`_build_candidates`, `_apply_llm_patch`) all set `"book_id"` in metadata, which A185/A184's prior work already established they do.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/plan_vetter.py` | `vet()` and `_choose_alternative()`: resolve lookup key via `metadata.get("book_id") or action_id`, not `action_id` alone |
| `tests/test_a188_vetter_keys_by_book_id.py` (new) | Regression coverage (see Tests) |

## Tests

New `tests/test_a188_vetter_keys_by_book_id.py`:

1. This card's exact live reproduction: `action_attempt_counts`/`action_falsification_counts` populated under 6 distinct `ACTION6@x,y` book_ids (matching the card's numbers), candidate for a 7th fresh coordinate — assert `vet()`'s metadata correctly reports that 7th coordinate's own (zero) history, and separately assert a candidate for one of the *already-falsified* coordinates correctly reports its real attempts/falsifications.
2. `repeated_falsification_threshold` veto fires for a click-target candidate whose own book_id has been falsified `>= 2` times (previously could never fire for `ACTION6`).
3. `excessive_repetition_threshold` veto fires for a click-target candidate whose own book_id has been attempted `>= 3` times with `score < weak_evidence_score_threshold`.
4. `_choose_alternative` does not return an `ACTION6` alternative whose own book_id already has attempts > 0.
5. Regression guard: non-click actions (`ACTION1`-`ACTION5`, where `book_id == action_id`) behave identically before and after this change — reuse/adapt any existing vetter tests covering `repeated_falsification_threshold`/`excessive_repetition_threshold` for non-click actions.

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a188_vetter_keys_by_book_id.py -v
make test-a
make test-all
```

Live confirmation: `campy status` health check, then a smoke run (self-capped; consider a larger `--max-steps` given this bug only manifests once a click-target action accumulates real per-book_id history, best-effort per the established caveat from A182/A184/A185/A187).

## Assumptions/defaults

- `candidate.metadata["book_id"]` is always populated by the time a candidate reaches the vetter — established by A184/A185's prior work on `plan_generator.py`; if any candidate-construction path is found missing it during implementation, fix that path too rather than adding a vetter-side workaround.
- This card is scoped to `plan_vetter.py` only. A189 (found the same session) covers the complementary `_apply_llm_patch` family-matching issue in `plan_generator.py` — the two are independently fixable and independently testable.
