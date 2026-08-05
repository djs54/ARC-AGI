# Plan: A155 — LLM Free-Text Fallback Parser Manufactures Goal IDs From Raw Prose

## Context

`agents/arc4/goal_resolver.py::_parse_llm_response` (current lines 436-455) has two paths: a clean-JSON path (hardened by A154 to require a truthy `goal_id`) and a free-text regex fallback used when the response isn't valid JSON. The fallback's `reason_match = re.search(r"reason\s*[:=]\s*(.+)", response, re.IGNORECASE)` has no length bound and, for free-form Ollama prose, typically captures from the first occurrence of "reason" to the end of the string — i.e. most of the response.

`_merge_llm_patch` (current lines 231-269) then does, when no `goal_id` was found:

```python
goal_id=goal_id or self._slugify(reason or "llm-goal"),
```

turning that captured prose blob into the hypothesis's `goal_id`. Live evidence: `artifacts/submission_results_single.live.jsonl`, game `sp80-589a99af`, 2026-08-04 — `active_goal_hypothesis_id` was a 300+ word slugified paragraph on 6 of 10 steps, and it won selection over real candidates because it inherited a confidence value the regex scraped from elsewhere in the same prose.

## Implementation Steps

### Step 1: Stop manufacturing a goal id from `reason` in `_merge_llm_patch`

Current (lines 258-267):

```python
if not matched:
    updated.append(
        GoalHypothesis(
            goal_id=goal_id or self._slugify(reason or "llm-goal"),
            description=patch.get("description") or reason or "LLM-backed goal hypothesis",
            confidence=confidence,
            evidence=evidence,
            metadata={"tier": 3, "llm_reason": reason, "llm_patch": True},
        )
    )

return updated
```

Change to:

```python
if not matched and goal_id:
    updated.append(
        GoalHypothesis(
            goal_id=goal_id,
            description=patch.get("description") or reason or "LLM-backed goal hypothesis",
            confidence=confidence,
            evidence=evidence,
            metadata={"tier": 3, "llm_reason": reason, "llm_patch": True},
        )
    )

return updated
```

The only change is the `if not matched:` guard becoming `if not matched and goal_id:`, and `goal_id=goal_id or self._slugify(...)` becoming `goal_id=goal_id` (safe now that the guard ensures it's truthy). No other line in this method changes — the "goal_id matches an existing hypothesis" merge branch (lines 242-256) is untouched and still applies its update correctly when `goal_id` is a real, matched id.

### Step 2: Bound the `reason` capture in `_parse_llm_response`

Current (line 450):

```python
reason_match = re.search(r"reason\s*[:=]\s*(.+)", response, re.IGNORECASE)
```

Change the capture group to a bounded quantifier:

```python
reason_match = re.search(r"reason\s*[:=]\s*(.{1,200})", response, re.IGNORECASE)
```

This caps the captured reason at 200 characters (plenty for a short rationale, nowhere near enough to swallow a multi-hundred-word response). No other line in `_parse_llm_response` needs to change — the returned dict still uses `reason_match.group(1).strip()` as before, just now bounded at the regex level. Note: since `goal_match` is independent of `reason_match`, this alone does not fix the goal-id manufacturing bug — Step 1 is the actual fix; this step is defense-in-depth so `llm_reason` metadata / Temporal state can't balloon even in the "no `goal_id`, patch legitimately dropped" case, since `reason` still gets logged in the dropped patch (not stored on a hypothesis, but still worth bounding for telemetry/log hygiene).

### Step 3: Tests

New file `tests/test_a155_llm_freetext_fallback_goal_id.py`. Reuse the `RecordingLLMPort` stub pattern from `tests/test_arc4_goal_resolver.py` (read that file first — it's a small dataclass with `.response` and a `chat()` method recording calls) for the `resolve()`-level integration tests; call `GoalResolver._parse_llm_response` / `_merge_llm_patch` directly for the unit-level tests.

1. `test_freetext_response_without_goal_id_does_not_create_hypothesis` — a response fixture shaped like the actual Ollama output from the live run (long free-text reasoning containing the word "reason" partway through, no clean `goal_id:` substring) fed through `_parse_llm_response` then `_merge_llm_patch` on a baseline hypothesis list — assert the hypothesis list is unchanged (same length, same goal_ids) after the merge.
2. `test_freetext_response_with_clean_goal_id_still_works` — a free-text (non-JSON) response that DOES contain a clean `goal_id: some-real-id` substring — assert `_parse_llm_response` extracts it and `_merge_llm_patch` correctly creates/merges the hypothesis (regression guard: Step 1 must not break the legitimate free-text-with-id path).
3. `test_reason_capture_is_bounded` — a response with "reason: " followed by 1000+ characters of text — assert `_parse_llm_response(response)["reason"]` (only reachable via a request that also has a goal_id or confidence match, per the `if goal_match or confidence_match or reason_match:` guard — construct the fixture accordingly) has length <= 200.
4. `test_resolve_end_to_end_no_garbage_goal_after_freetext_llm_response` — full `resolve()` call with a stubbed `llm_port` returning the actual (or close to actual) long free-text prose from the live smoke evidence, hypotheses ambiguous enough to trigger escalation — assert the selected/resulting hypotheses contain no `goal_id` longer than a reasonable bound (e.g. assert no hypothesis's `goal_id` exceeds 100 characters) and that the real candidate hypotheses are unaffected.

### Step 4: Regression checks

```bash
.venv/bin/python -m pytest tests/test_arc4_goal_resolver.py -v
.venv/bin/python -m pytest tests/test_a154_llm_adapter_failure_visibility.py -v
```

Confirm both pass unchanged — Step 1's guard only removes the *fallback-with-no-goal_id* branch; every existing test that exercises a well-formed patch (with `goal_id`) is unaffected.

## Verify

```bash
.venv/bin/python -m pytest tests/test_a155_llm_freetext_fallback_goal_id.py -v
.venv/bin/python -m pytest tests/test_arc4_goal_resolver.py tests/test_a154_llm_adapter_failure_visibility.py -q
make test-a
make test-all
```

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/goal_resolver.py` | `_merge_llm_patch`: guard the no-match append on `goal_id` truthy, drop the `_slugify(reason)` fallback; `_parse_llm_response`: bound `reason_match`'s capture group to 200 chars |
| `tests/test_a155_llm_freetext_fallback_goal_id.py` | New, 4 tests |

## Risks

- Very low risk — this narrows an already-narrow fallback path (only reached when JSON parsing fails), and the fix is subtractive (stop manufacturing something) rather than adding new logic surface.
- Does not address why Ollama returns free text instead of clean JSON in the first place (prompt/JSON-mode tuning) — explicitly out of scope, noted in the card.
