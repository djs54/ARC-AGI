# Plan: A181 — Cap and Bound the Local Ollama Goal-Disambiguation Call

## Card metadata

- ID: A181
- Priority: P2
- Layer: transport/client seam
- Dependencies: none

## Summary

`arc_runtime/llm.py::LLMClient.chat`/`achat` never sets `max_tokens`, and the configured `timeout_seconds` (default 180s for the `ollama` provider) does not appear to actually abort a hung request in practice — observed twice live (17,000+ decoded tokens after 9+ minutes; ~1,176 tokens after ~50s on a different puzzle), confirmed via Ollama's own `~/.ollama/logs/server.log` `n_decoded` counter, not assumed.

## Implementation approach

1. Read `arc_runtime/llm.py` in full, including `create_llm_client` and how `_resolve_timeout_seconds`/`_resolve_max_retries`'s return values actually flow into the constructed client (or don't — confirm this precisely before assuming where the bug is).
2. Add an explicit `max_tokens` (or Ollama's `num_predict` via `extra_body`, whichever the OpenAI-compatible client surfaces) to the `chat()` call — pick a cap generous enough for the largest legitimate response this call path produces (a short JSON object plus reasoning text) but bounded (e.g. 512-1024 tokens as a starting point; adjust based on what `achat`'s actual callers need).
3. Determine why the configured timeout isn't aborting the observed hangs: check whether `timeout_seconds`/`max_retries` are passed into the client constructor at all, and if so, whether the underlying transport (httpx) is honoring a read timeout on a non-streaming request that's actively receiving data (a "the server is still sending, so it's not idle" edge case is a plausible explanation worth checking specifically).
4. Consider whether prompt size (both observed hangs involved a 64x64 grid's full `grid_text`) is a contributing factor, but do not make the token cap conditional on prompt size — it should be an unconditional safety bound regardless of cause.

## Concrete file changes

| File | Change |
|------|--------|
| `arc_runtime/llm.py` | Add `max_tokens` cap to `chat()`/`achat()`; fix or confirm timeout enforcement |
| `tests/test_a181_*.py` (new) | Regression coverage (see below) |

## Tests

- A test using a mock/fake OpenAI-compatible client that simulates a non-terminating streamed/long completion, asserting the real client call still returns (raises a timeout, or is capped) within a bounded time rather than hanging indefinitely.
- A test asserting `max_tokens` (or the provider-equivalent) is present in the actual request payload sent to the completions endpoint.
- If the timeout-enforcement root cause turns out to be a real bug in how config values reach the client, add a regression test for that specifically once identified.

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a181_*.py -v
make test-a
make test-all
```

Live confirmation: force or wait for a real goal-ambiguity escalation (`campy status` health check first), confirm the call completes within a bounded time regardless of grid size, using the same self-capped-wait methodology established this session.

## Assumptions/defaults

- This plan is intentionally lighter on specifics than other A### plans because the root cause (missing `max_tokens`, and why the timeout isn't firing) needs direct investigation of `arc_runtime/llm.py`'s actual behavior before a precise fix can be specified — treat step 1 and step 3 above as required investigation before implementation, not optional context.
