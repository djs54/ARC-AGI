# Plan: A168 — Fix `plan_generator.py`'s Silently-Discarded LLM Responses

## Context

`plan_generator.py::_query_llm` requires a raw-JSON-only response and has no fallback; `llama3.1:8b` (the local Ollama model used in live-smoke) reliably replies in prose instead, so every LLM escalation this session has been a no-op. Two independent fixes, both verified live against the running Ollama endpoint before implementation:

1. `response_format: {"type": "json_object"}` on the OpenAI-compatible `chat.completions.create()` call makes Ollama return valid JSON, but the model still needs an explicit key-name instruction in the system prompt to use `action_id`/`reason` (not `best_action_id`/`why`).
2. Even with JSON mode, a regex + candidate-string-scan fallback is worth adding as defense in depth (mirrors `goal_resolver.py::_parse_llm_response`'s existing pattern, which already solves half of this for the goal-resolution call site).

## Implementation

### 1. JSON-mode enforcement in `arc_runtime/llm.py::LLMClient`

```python
async def achat(self, messages: list[dict]) -> str:
    import asyncio

    try:
        return await asyncio.to_thread(self.chat, messages, response_format={"type": "json_object"})
    except Exception:
        return await asyncio.to_thread(self.chat, messages)
```

`LLMClient.chat()` already accepts `**kwargs` and passes them through to `self._client.chat.completions.create(...)` — no change needed there. The `try/except` covers providers/APIs that reject the `response_format` param (not universally supported across OpenAI-compatible shims).

Do not touch `arc_runtime/bundle.py::SyncLLMPortAdapter.chat()` — its `achat = getattr(self._llm_client, "achat", None)` call site calls `achat(message_dicts)` with a single positional arg; existing test stubs (`tests/test_a154_llm_adapter_failure_visibility.py`'s `_FailingAchatClient` etc.) define `achat(self, message_dicts)` with that exact signature, so keeping `LLMClient.achat()`'s public signature unchanged (still just `messages`) avoids any risk of breaking those stubs while still getting JSON-mode enforcement for the one concrete production client that matters (`arc_runtime/llm.py::LLMClient`, used with Ollama/OpenAI/Anthropic/Google in production).

### 2. Tighten `plan_generator.py`'s system prompt

In `_query_llm` (current ~L379-395):

```python
LLMMessage(
    role="system",
    content="Pick the best ARC action_id and explain why it should be tried next.",
),
```

becomes:

```python
LLMMessage(
    role="system",
    content=(
        "Pick the best ARC action_id and explain why it should be tried next. "
        "Respond with ONLY a JSON object with exactly these keys: "
        '"action_id" (string, must match one of the candidate action_ids exactly) '
        'and "reason" (string, brief explanation).'
    ),
),
```

Verified live this produces `{"action_id": "ACTION7", "reason": "..."}` reliably against `llama3.1:8b`.

### 3. Fallback parsing in `plan_generator.py`

Add `import re` to the top-of-file imports. Extract the parse logic from `_query_llm` into a new `_parse_llm_response` staticmethod (mirrors `goal_resolver.py`'s naming/shape), called with the candidate list so it can scan for literal action_id mentions:

```python
def _query_llm(
    self,
    llm_port: LLMPort,
    perception: PerceptionSnapshot,
    goal: ResolvedGoal,
    candidates: Sequence[_CandidateRecord],
) -> dict[str, Any] | None:
    messages = [ ... ]  # system prompt updated per step 2
    response = llm_port.chat(messages)
    return self._parse_llm_response(response, candidates)

@staticmethod
def _parse_llm_response(response: str, candidates: Sequence[_CandidateRecord]) -> dict[str, Any] | None:
    if not response:
        return None
    try:
        parsed = json.loads(response)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, Mapping) and parsed.get("action_id"):
        return dict(parsed)

    action_match = re.search(r"action[_\s-]*id\s*[:=]\s*\"?([A-Za-z0-9_.,@-]+)\"?", response, re.IGNORECASE)
    action_id = action_match.group(1) if action_match else None

    if action_id is None:
        # Prose responses (e.g. `The best ARC action_id to try next would be "ACTION7".`)
        # mention a real candidate's action_id without a key:value shape a pure
        # regex would catch -- scan for a literal, word-bounded mention instead.
        # Longest-id-first avoids a short id false-matching inside a longer one
        # (e.g. "ACTION1" inside "ACTION10").
        candidate_ids = sorted({c.action_id for c in candidates}, key=len, reverse=True)
        for candidate_id in candidate_ids:
            if re.search(rf"\b{re.escape(candidate_id)}\b", response):
                action_id = candidate_id
                break

    if action_id is None:
        return None

    reason_match = re.search(r"reason\s*[:=]\s*(.{1,200})", response, re.IGNORECASE)
    return {
        "action_id": action_id,
        "reason": reason_match.group(1).strip() if reason_match else response.strip()[:200],
    }
```

`_apply_llm_patch` (unchanged) already handles `patch.get("action_id")`/`patch.get("reason")` correctly regardless of which path produced them.

## Tests

New tests in `tests/test_a168_plan_generator_llm_response_discarded.py`:

1. `test_prose_response_extracts_action_id_via_candidate_scan` — the exact reproduction text from the card's Problem section (`"The best ARC action_id to try next would be \"ACTION7\"..."`) against real `_CandidateRecord`s for `ACTION6`/`ACTION7` — assert `_parse_llm_response(...)["action_id"] == "ACTION7"`.
2. `test_well_formed_json_still_works_directly` — regression guard: `'{"action_id": "ACTION7", "reason": "x"}'` still parses via the direct path.
3. `test_response_mentioning_no_candidate_returns_none` — prose that doesn't mention any real candidate action_id anywhere returns `None` (doesn't hallucinate a match).
4. `test_key_value_shaped_prose_matches_via_regex` — `"action_id: ACTION6 because it's untested"` matches via the regex path (not just the candidate-scan path) — proves both fallback layers work independently.
5. `test_apply_llm_patch_actually_invoked_end_to_end` — construct a `PlanGenerator`, a stub `llm_port` returning the exact prose reproduction text, low-scoring candidates (so escalation triggers), call `generate(...)`, and assert the winning candidate's metadata has `llm_guidance: True` — the missing end-to-end proof that a unit-level parser fix actually changes real behavior.

`tests/test_a154_llm_adapter_failure_visibility.py`: run unchanged, confirm still green (proves the `LLMClient.achat` JSON-mode change doesn't affect these stub-based tests, since they don't use `arc_runtime.llm.LLMClient` at all — they construct their own minimal stub classes).

## Verify

```bash
.venv/bin/python -m pytest tests/test_a168_plan_generator_llm_response_discarded.py -v
.venv/bin/python -m pytest tests/test_a154_llm_adapter_failure_visibility.py -v
make test-a
make test-all
```

Live confirmation (required — this bug was found live, not by unit tests):

```bash
CAMPY_MCP_CMD="../hippocampy/.venv/bin/python -m campy.adapters.mcp_server" \
  PYTHONPATH=. .venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 15
grep -c '"llm_guidance": true' <log>
```
Confirm at least one nonzero occurrence (was 0 before this fix, across every run this session).

## Files Modified

| File | Change |
|------|--------|
| `arc_runtime/llm.py` | `LLMClient.achat` requests JSON mode with graceful fallback |
| `agents/arc4/plan_generator.py` | System prompt tightened; `_query_llm` refactored to delegate to new `_parse_llm_response` with regex + candidate-scan fallback; `import re` added |
| `tests/test_a168_plan_generator_llm_response_discarded.py` | New, 5 tests |

## Risks

- JSON-mode `response_format` support varies by provider — mitigated by the try/except fallback in `LLMClient.achat`; worth a quick manual sanity check that OpenAI/Anthropic/Google's OpenAI-compat endpoints don't hard-reject the param in a way that isn't a catchable exception (unlikely, but not verified live for those three providers in this pass — only Ollama was).
- The candidate-string-scan fallback could theoretically false-positive if a candidate's `action_id` happens to appear as a substring of unrelated prose (e.g., discussing why an action *wasn't* chosen) — accepted as a reasonable tradeoff since the existing regex-first ordering means this only kicks in when no cleaner signal exists, and a wrong-but-valid action_id is strictly better than the current "always discard everything" status quo.
