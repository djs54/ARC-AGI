# Plan: A174 — Fix Sync Fallback's String-vs-Structured-Messages Mismatch

## Context

`SyncLLMPortAdapter.chat()`'s sync-method fallback loop calls every candidate method (`generate`/`complete`/`chat`) with a single joined string prompt. `LLMClient.chat()` (the real production client) expects structured `messages` (same contract as its own `achat()`), so when the fallback reaches it, the request sent to Ollama has `messages` as a plain string, which the server correctly rejects.

## Implementation

In `arc_runtime/bundle.py::SyncLLMPortAdapter.chat()`, in the sync-fallback loop:

```python
prompt = "\n".join(f"{m.role}: {m.content}" for m in messages)
for method_name in ("generate", "complete", "chat"):
    method = getattr(self._llm_client, method_name, None)
    if method is None:
        continue
    try:
        call_arg = message_dicts if method_name == "chat" else prompt
        result = method(call_arg)
```

`chat` (mirroring `achat`'s structured-messages contract) gets `message_dicts` (the list of `{"role", "content"}` dicts already built earlier in this method for the `achat` path). `generate`/`complete` keep receiving the joined string `prompt`, preserving the existing tested contract for those method names.

## Tests

New tests in `tests/test_a154_llm_adapter_failure_visibility.py` (the existing file covering `SyncLLMPortAdapter` failure-mode behavior) or a new `tests/test_a174_sync_fallback_structured_messages.py`:

1. `test_chat_fallback_receives_structured_messages_not_string` — a stub client with a `chat(self, messages)` method (no `achat`) that asserts `isinstance(messages, list)` and returns a canned response — call `adapter.chat([...])`, assert no exception and the canned response is returned.
2. `test_achat_failure_falls_back_to_chat_with_structured_messages` — a stub client whose `achat` always raises, and whose `chat(self, messages)` asserts `isinstance(messages, list)` — confirms the fallback path specifically (not just the direct-chat path) passes structured messages.
3. Regression: existing `generate`/`complete`-based tests continue to receive a string prompt unchanged (`tests/test_arc4_integration.py::FakeLLMClient.generate`, `tests/test_a154_llm_adapter_failure_visibility.py`'s `_FailingSyncMethodClient.generate`).

## Verify

```bash
.venv/bin/python -m pytest tests/test_a154_llm_adapter_failure_visibility.py tests/test_arc4_integration.py -v
# plus the new A174 test(s)
make test-a
make test-all
```

Live confirmation: re-run A169's `--live-smoke` verification; confirm no `"cannot unmarshal string"` errors appear even if `achat` still occasionally fails for other reasons.

## Files Modified

| File | Change |
|------|--------|
| `arc_runtime/bundle.py` | `SyncLLMPortAdapter.chat()`'s sync-fallback loop passes structured messages to `chat`, string to `generate`/`complete` |
| New/updated test file | 2-3 new tests |

## Risks

- Very low — this is a pure bug fix restoring the fallback to a shape that actually matches `LLMClient`'s real interface; the only behavior change is for the previously-broken `chat`-fallback case, which never worked correctly before this fix.
