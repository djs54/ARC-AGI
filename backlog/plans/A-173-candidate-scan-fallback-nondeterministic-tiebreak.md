# Plan: A173 — Fix A168's Nondeterministic Candidate-Scan Fallback

## Context

`_parse_llm_response`'s candidate-scan fallback builds its candidate list from a `set`, so equal-length action-id ties resolve based on Python's per-process randomized string hashing, not any real signal. Fix: order-preserving dedup, tie-break by occurrence count.

## Implementation

In `agents/arc4/plan_generator.py::_parse_llm_response`:

```python
if action_id is None:
    seen: list[str] = []
    for candidate in candidates:
        if candidate.action_id not in seen:
            seen.append(candidate.action_id)
    seen.sort(key=len, reverse=True)

    best_id: str | None = None
    best_count = 0
    for candidate_id in seen:
        count = len(re.findall(rf"\b{re.escape(candidate_id)}\b", response))
        if count > best_count:
            best_count = count
            best_id = candidate_id
    action_id = best_id
```

`seen` preserves the order candidates were passed in (deterministic, no set). `sort(key=len, reverse=True)` is a stable sort, so equal-length ties keep their original relative order as a final deterministic tiebreak if occurrence counts are *also* equal. The real tiebreak driver is occurrence count via `re.findall`.

## Tests

In `tests/test_a168_plan_generator_llm_response_discarded.py`, add:

```python
def test_more_frequently_mentioned_candidate_wins_length_tie(self):
    candidates = [_StubCandidate("ACTION6"), _StubCandidate("ACTION7")]
    response = "ACTION7 seems good. ACTION7 again. Consider ACTION6 too."
    parsed = PlanGenerator._parse_llm_response(response, candidates)
    assert parsed["action_id"] == "ACTION7"
```

Re-run the existing `test_prose_response_extracts_action_id_via_candidate_scan` and `test_apply_llm_patch_actually_invoked_end_to_end` in a loop of separate process invocations to confirm the flake is gone:

```bash
for i in $(seq 1 10); do .venv/bin/python -m pytest tests/test_a168_plan_generator_llm_response_discarded.py -q || break; done
```

## Verify

```bash
.venv/bin/python -m pytest tests/test_a168_plan_generator_llm_response_discarded.py -v
for i in $(seq 1 10); do .venv/bin/python -m pytest tests/test_a168_plan_generator_llm_response_discarded.py -q || echo "FLAKE on run $i"; done
make test-a
make test-all
```

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/plan_generator.py` | `_parse_llm_response`'s candidate-scan fallback rewritten for deterministic tie-breaking |
| `tests/test_a168_plan_generator_llm_response_discarded.py` | +1 test for the occurrence-count tiebreak |

## Risks

- None beyond the fix itself — purely a determinism correction, no behavior change for the non-tied case (single matching candidate, which is the common case).
