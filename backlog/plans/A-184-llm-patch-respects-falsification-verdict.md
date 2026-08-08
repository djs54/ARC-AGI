# Plan: A184 — LLM Patch Must Respect the Graph's Falsification Verdict

## Card metadata

- ID: A184
- Priority: P1
- Layer: ARC runtime
- Dependencies: A182

## Summary

`agents/arc4/plan_generator.py::_apply_llm_patch` grants any LLM-picked candidate a score floor of `bonus + replan_feedback_bonus` (`bonus` is always `0.0` in practice, since the escalation prompt never asks the LLM for a confidence value — so effectively a flat `0.3` floor), with no check against the candidate's own `repeated_falsified` metadata. Confirmed live: this let an action already falsified twice (deeply negative score) get re-selected via LLM guidance, directly causing a premature `second_veto` episode termination with untested actions still available.

## Technical approach

1. Read `agents/arc4/plan_generator.py::_apply_llm_patch` (lines ~531-567) in full, and confirm `_CandidateRecord.metadata["repeated_falsified"]` is set correctly for every real candidate before this method runs (it's set in `_build_candidates`, confirm the exact key name and boolean semantics match what's checked here — don't assume, verify against the current source).
2. In the "matched" branch (the LLM's `action_id` corresponds to an existing candidate) add a check: if `candidate.metadata.get("repeated_falsified")` is truthy, construct the updated `_CandidateRecord` with `score=candidate.score` (unchanged, not overridden) instead of `max(candidate.score, bonus + self._limits.replan_feedback_bonus)`. Still append the LLM's `reason` to the rationale and set `metadata["llm_guidance"] = True`, `metadata["llm_reason"] = reason`, and add `metadata["llm_guidance_overridden"] = True` so the override decision is visible in traces and tests, not a silent no-op.
3. Leave the "not matched" branch (LLM picks an `action_id` outside the current candidate list) unchanged — no `repeated_falsified` metadata exists to check for a synthetic candidate, and this is a narrower, separate scenario out of scope for this card.
4. Do not touch `_query_llm`'s prompt construction, `llm_low_score_threshold`, or any other scoring constant — this card is scoped to the one gap (missing check in `_apply_llm_patch`), not a broader rework of the escalation path.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/plan_generator.py` | `_apply_llm_patch`'s matched branch: skip the score-floor override when `repeated_falsified` is true, preserve rationale/reason, tag `llm_guidance_overridden` |
| `tests/test_a184_llm_patch_respects_falsification.py` (new) | Regression coverage (see Tests) |

## Tests

New `tests/test_a184_llm_patch_respects_falsification.py`, testing `PlanGenerator._apply_llm_patch` directly (it's a private method but the most precise way to test this exact logic in isolation — mirror how other private-method-level tests in this codebase are justified when the public API path would obscure the exact assertion, otherwise go through `generate()` with a stub `llm_port` if that proves cleaner once the code is in front of you):

1. LLM picks a candidate with `metadata["repeated_falsified"] = True` and a deeply negative score (e.g. `-0.48`, matching this card's live reproduction) — assert the resulting score is unchanged (still `-0.48`, not bumped to `>= 0.3`), and `metadata["llm_guidance_overridden"] is True`.
2. LLM picks a candidate WITHOUT `repeated_falsified` (or `False`/absent) and a low score — assert the existing floor-granting behavior is unchanged (score becomes `>= replan_feedback_bonus`), confirming this card doesn't break the normal escalation path.
3. The LLM's `reason` text is still present in the resulting candidate's rationale even when overridden (transparency requirement from the card).
4. The "not matched" branch (LLM picks an action_id not in the candidate list) is unaffected — still creates a synthetic candidate with the existing floor logic, regression guard that this card didn't touch it.
5. End-to-end regression via `PlanGenerator.generate(..., llm_port=stub)`: a repeatedly-falsified action with a stub LLM port that always picks it does NOT end up as the top-ranked candidate when better (untested, positively-scored) alternatives exist in the same candidate list.

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a184_llm_patch_respects_falsification.py -v
make test-a
make test-all
```

Live confirmation: `campy status` health check, then `make smoke` (self-capped per this session's methodology). Best-effort — depends on hitting a live scenario where an action gets falsified twice and the LLM is escalated to again, same caveat noted in A182's plan. If a repeated-falsification + LLM-escalation combination doesn't occur in that particular run, the unit tests are the primary verification; note this honestly rather than blocking on a specific run outcome.

## Assumptions/defaults

- `candidate.metadata["repeated_falsified"]` (set in `_build_candidates`, `falsifications >= 2`) is the correct, existing signal to gate on — no new threshold or flag is introduced.
- The fix preserves the LLM's reasoning text even when its score-floor request is denied, on the principle that the LLM's input should remain visible/debuggable, not silently discarded — this is a design choice worth confirming reads correctly in the implementation, not just assumed.
