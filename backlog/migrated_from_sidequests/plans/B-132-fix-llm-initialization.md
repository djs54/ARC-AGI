# B132 Plan: Fix LLM Initialization — openai Package Not Installed

## Summary

Install the missing `openai` package into the project `.venv`, add it to dependency manifests, and add fail-fast guards so future missing-package errors surface loudly rather than silently degrading to hardcoded fallbacks.

## Current State

- `.venv` is missing the `openai` package.
- `LLMProvider.__init__` catches the import error silently and leaves `self.llm = None`.
- `_mental_sandbox()` and `_query_llm()` both crash on `self.llm.chat(...)` → caught → return hardcoded `"ACTION1"` fallback on every step.
- Agent runs 15 steps with zero real reasoning. Root cause of B133.

## Technical Approach

### Step 1: Install missing package

```bash
cd sidequests-brain
.venv/bin/pip install "openai>=1.0.0"
```

Verify:
```bash
.venv/bin/python -c "import openai; print(openai.__version__)"
```

### Step 2: Persist to dependency manifests

In `requirements.txt`, add:
```
openai>=1.0.0
```

In `pyproject.toml` (under `[project] dependencies` or `[tool.poetry.dependencies]`), add:
```
openai = ">=1.0.0"
```

### Step 3: Add None-guard in orchestrator

In `agents/arc3/orchestrator.py`, locate `_mental_sandbox()`. Before the `self.llm.chat(...)` call, add:

```python
if self.llm is None:
    self._emit_trace_event("llm_unavailable", {
        "phase": "mental_sandbox",
        "reason": "LLM provider not initialized — check openai/anthropic installation"
    })
    raise RuntimeError("LLM provider is None; cannot run mental_sandbox")
```

Remove the broad `except Exception` that silently swallows this and returns `"ACTION1"`. Let it propagate so the caller handles it explicitly.

Similarly in `_query_llm()`:
```python
if self.llm is None:
    raise RuntimeError("LLM provider is None; _query_llm cannot proceed")
```

### Step 4: Improve error surfacing in LLMProvider

In `agents/arc3/llm_provider.py` (or wherever `LLMProvider` is defined), change the silent `self.llm = None` path to log at `ERROR` level with a clear message:

```python
except ImportError as e:
    logger.error(
        "[LLM] FATAL: Could not initialize provider '%s': %s. "
        "Run: .venv/bin/pip install openai anthropic",
        provider_name, e
    )
    self.llm = None  # keep assignment but log loudly
```

## Concrete File Changes

| File | Change |
|------|--------|
| `requirements.txt` | Add `openai>=1.0.0` |
| `pyproject.toml` | Add `openai` to dependencies |
| `agents/arc3/orchestrator.py` | Add `llm is None` guard + new `llm_unavailable` trace event in `_mental_sandbox` and `_query_llm` |
| `agents/arc3/llm_provider.py` | Upgrade silent catch to `logger.error(...)` with actionable message |

## Validation Commands

```bash
# Verify package installed
.venv/bin/python -c "import openai; print('openai', openai.__version__)"

# Run a live smoke test (needs ARC_API_KEY)
ARC_API_KEY="$(jq -r '.key' benchmarks/arc3/.arc/arc.json)" \
  .venv/bin/python run_single_puzzle.py --puzzle-id 007bbfb7 --max-steps 3

# Confirm no mental_sandbox_parse_error in output
jq '[.[] | select(.event == "mental_sandbox_parse_error")] | length' master_timeline.json

# Run tests
cd sidequests-brain && .venv/bin/pytest -q tests/
```

## Acceptance Criteria (checklist)

- [ ] `import openai` succeeds in `.venv`
- [ ] `openai` present in `requirements.txt` and `pyproject.toml`
- [ ] Zero `mental_sandbox_parse_error` events in next smoke test run
- [ ] `master_timeline.json` contains `mental_sandbox_result` events with real model output
- [ ] `llm_unavailable` trace event fires (instead of silent fallback) when LLM is intentionally misconfigured
- [ ] `pytest -q tests/` passes

## Notes / Risks

- If the project uses `ollama` as LLM backend, `openai` is still the underlying SDK; confirm `OPENAI_API_KEY` or `OPENAI_BASE_URL` is set for the target model.
- Do not remove the `except` block in `_mental_sandbox` entirely — there are other legitimate parse errors. Replace it with a targeted `if self.llm is None` pre-check before the try block.
- This is a prerequisite for B133. Do not attempt B133 until this card is validated.
