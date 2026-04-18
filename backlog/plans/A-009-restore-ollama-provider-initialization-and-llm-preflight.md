# A-009 - Restore Ollama Provider Initialization and LLM Preflight

## Card metadata

- Card: A009
- Priority: P0
- Depends on: A004, A005, A006

## Summary

Make ARC's `provider=ollama` runtime explicit and fail-fast. In the current architecture, the Ollama client still uses the OpenAI-compatible Python SDK, so missing `openai` must be surfaced as a startup/preflight failure instead of a later degraded-mode surprise.

## Implementation approach

- audit the ARC-owned LLM factory and startup entrypoints
- make the runtime dependency chain explicit in user-facing error messages
- add a preflight/startup check for required Python package availability when `provider=ollama`
- ensure packaging metadata and validation scripts match the actual runtime requirement
- prefer fail-fast behavior over silent degraded mode when the configured provider cannot initialize in execution paths that require an LLM

## Concrete file additions/edits

- edit `arc_runtime/llm.py`
  - improve provider initialization error handling
  - make the Ollama/OpenAI-compatible SDK dependency explicit
- edit `pyproject.toml`
  - verify dependency declaration remains correct
- edit `run_single_puzzle.py` and any related preflight path
  - fail early when configured LLM provider cannot initialize
- add or update focused tests for dependency/preflight messaging

## API/interface changes

- startup/preflight paths should return a clear, actionable failure when Ollama cannot initialize
- the message should explain that the local Ollama path still uses the `openai` Python package in this repo architecture

## Tests to add or run

- targeted tests for `arc_runtime.llm.create_llm_client`
- CLI or preflight tests for fail-fast behavior
- one local validation command that proves `provider=ollama` initializes successfully in the intended runtime

## Validation commands

- `pytest -q tests/test_run_single_puzzle_cli.py`
- any focused LLM runtime tests added by the implementation
- `/Users/djshelton/Desktop/GitProjects/ARC_AGI/.venv/bin/python -c "from arc_runtime.llm import create_llm_client; ..."`

## Assumptions/defaults

- `ollama` remains the intended local provider for ARC smoke runs
- this repo should not pretend that `ollama` mode is SDK-free when the implementation still depends on `openai`
- clear preflight failure is preferable to a degraded run that later times out or crashes
