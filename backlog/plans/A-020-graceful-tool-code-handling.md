# A-020 Graceful Tool Payload Handling Plan

## Card metadata

- Card: A020
- Title: Gracefully Handle Missing Tool Payloads in LLM Responses
- Priority: P1
- Layer: ARC runtime
- Depends on: A009

## Summary

Harden LLM tool-response validation so missing or empty sandbox tool payloads are reported as structured malformed-response conditions instead of surfacing as incidental crashes.

## Implementation approach

1. Locate the first runner/orchestrator path that consumes LLM-produced tool responses and expects a non-empty payload.
2. Add a small validation helper near that parsing boundary.
3. Treat missing, `None`, or blank tool payloads as malformed model output.
4. Record the failure using the existing trace/result conventions.
5. Do not change successful parsing for valid responses.

## Concrete file additions/edits

- Modify the response parsing boundary in `agents/arc3/runner.py` or the local module that owns LLM tool-call parsing.
- Add or update focused tests in the nearest existing runner/orchestrator test file.
- Update `agents/arc3/failure_taxonomy.py` only if no suitable malformed-response classification already exists.

## API/interface changes

None expected. This should be an internal validation and error-reporting hardening change.

## Tests to add or run

- Add a test where an LLM response omits or nulls a tool payload.
- Add a test where a tool payload is present but empty.
- Keep or add a control test where a valid tool payload still executes.

## Validation commands

```bash
make test-a
pytest -q tests/test_arc3_runner.py tests/test_arc3_orchestrator.py
```

If those exact files are not where the parser is covered, run the nearest focused test file plus `make test-a`.

## Assumptions/defaults

- Missing tool payloads are malformed model responses, not MCP tool timeouts.
- The fix must not reintroduce direct `mcp_engine.*` or `sidequests.*` runtime imports.
- Keep the handling local to the LLM response boundary so downstream execution code can continue assuming parsed tool calls are well-formed.

## Validation note

2026-04-21:

- `make test-a` passed 18/18.
- `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m pytest -q` passed 727/727.
- Plain `.venv/bin/python -m pytest -q` reached 726 passed / 1 failed because `test_upsert_lesson_round_trip` attempted Hugging Face metadata access under restricted network; the offline env is the correct local full-suite signal for the cached embedding model.
