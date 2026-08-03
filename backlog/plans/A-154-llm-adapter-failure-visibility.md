# Plan: A154 — LLM Adapter Silently Converts Failed Calls Into Valid-Looking Empty Patches

## Context

`arc_runtime/bundle.py::SyncLLMPortAdapter.chat()` (current lines 50-101) is the only implementation of the `LLMPort` protocol wired into the live-smoke path (`build_arc_v2_bundle`, line 127: `llm_port = SyncLLMPortAdapter(llm_client) if llm_client is not None else None` — always non-`None` when a client is configured, which it is in live smokes).

Three places in `chat()` collapse "the call failed" into the literal string `"{}"`:

```python
# line ~79-80, inside the achat try/except
except Exception:
    pass   # falls through to the sync-method loop below, no logging

# line ~95-96, inside the sync-method fallback loop
except Exception:
    return "{}"

# line ~98-101, when no achat/generate/complete/chat method exists at all
try:
    return json.dumps({}, default=self._json_default)
except Exception:
    return "{}"
```

Consumer side, `agents/arc4/goal_resolver.py::_parse_llm_response` (current lines 437-455) treats any JSON object as a valid patch:

```python
if isinstance(parsed, Mapping):
    return dict(parsed)
```

`json.loads("{}")` is a `Mapping` → returns `{}`, which is not `None`. Back in `resolve()` (current lines 46-51):

```python
if llm_port is not None and self._should_escalate_to_llm(state, hypotheses):
    llm_patch = self._query_llm(llm_port, perception, hypotheses)
    if llm_patch is not None:
        hypotheses = self._merge_llm_patch(hypotheses, llm_patch)
```

`{}` is not `None`, so `_merge_llm_patch(hypotheses, {})` runs (current lines ~227-265). `patch.get("goal_id")` is `None`, no existing hypothesis matches, and the `if not matched:` branch appends a spurious `goal_id="llm-goal"`, `confidence=0.0` hypothesis every time this fires — polluting the hypothesis list instead of leaving it alone, on top of never providing the intended signal.

Contrast with `agents/arc4/plan_generator.py::_query_llm`/`_apply_llm_patch`, which already guards against this: `parsed if isinstance(parsed, dict) and parsed.get("action_id") else None` — an empty `{}` correctly becomes `None` there. `goal_resolver.py` is missing the equivalent `goal_id` guard.

## Implementation Steps

### Step 1: Stop returning `"{}"` on failure in `bundle.py`

Add a module-level logger near the top of `arc_runtime/bundle.py` (after the existing imports, before `ArcV2Bundle`):

```python
import logging

logger = logging.getLogger(__name__)
```

In `SyncLLMPortAdapter.chat()`:

1. The `achat` exception swallow (current lines 79-80):
```python
except Exception:
    pass
```
becomes:
```python
except Exception as exc:
    logger.warning("SyncLLMPortAdapter: achat call failed, falling back to sync methods: %s", exc)
```

2. The sync-method-loop exception (current lines 95-96):
```python
except Exception:
    return "{}"
```
becomes:
```python
except Exception as exc:
    logger.warning("SyncLLMPortAdapter: %s() call failed: %s", method_name, exc)
    return ""
```

3. The no-method-found fallback (current lines 98-101):
```python
try:
    return json.dumps({}, default=self._json_default)
except Exception:
    return "{}"
```
becomes:
```python
logger.warning("SyncLLMPortAdapter: llm_client %r has no achat/generate/complete/chat method", self._llm_client)
return ""
```
(Drop the `try/json.dumps({})` — there's nothing to serialize; `""` is the correct "no answer" signal and matches what `_parse_llm_response`'s `if not response: return None` already expects.)

### Step 2: Harden `_parse_llm_response` in `goal_resolver.py`

Current lines 437-446:
```python
@staticmethod
def _parse_llm_response(response: str) -> dict[str, Any] | None:
    if not response:
        return None
    try:
        parsed = json.loads(response)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, Mapping):
        return dict(parsed)
```
change the last two lines to:
```python
    if isinstance(parsed, Mapping) and parsed.get("goal_id"):
        return dict(parsed)
```
This leaves the regex-based fallback (current lines 448-455) as the next attempt for malformed-but-textual responses, unchanged — only a well-formed-JSON-but-missing-goal_id response now correctly falls through instead of short-circuiting as "valid."

### Step 3: Tests

New file `tests/test_a154_llm_adapter_failure_visibility.py`. Reuse the `RecordingLLMPort`-style stub pattern already established in `tests/test_arc4_goal_resolver.py` (a small dataclass with `.response`/`.calls` and a `chat()` method) where the test targets `goal_resolver.py`; for `bundle.py`-level tests, construct `SyncLLMPortAdapter` directly with a fake `llm_client` object.

1. `test_chat_returns_empty_string_when_achat_fails` — fake client whose `achat` raises `ConnectionError`, no `generate`/`complete`/sync `chat` methods present → `SyncLLMPortAdapter(client).chat(messages)` returns `""`, not `"{}"`.
2. `test_chat_returns_empty_string_when_sync_method_fails` — fake client with a `generate` method that raises → returns `""`.
3. `test_chat_returns_empty_string_when_no_method_exists` — fake client with none of `achat`/`generate`/`complete`/`chat` → returns `""`.
4. `test_chat_logs_warning_on_failure` — use `caplog` at `WARNING` level, assert at least one warning record is emitted for a failing `achat` call.
5. `test_parse_llm_response_empty_json_returns_none` — `GoalResolver._parse_llm_response("{}")` returns `None` (this is the direct regression test for the bug).
6. `test_parse_llm_response_with_goal_id_still_works` — `GoalResolver._parse_llm_response('{"goal_id": "g1", "confidence": 0.6}')` returns the dict unchanged (regression guard).
7. `test_resolve_does_not_append_spurious_hypothesis_on_empty_llm_response` — end-to-end through `resolve()`: two hypotheses close enough to trigger `_should_escalate_to_llm`, `llm_port` stubbed to return `"{}"` (simulating the exact failure this card fixes) → assert the resulting hypothesis list has no `goal_id == "llm-goal"` entry and no confidence-0.0 entry that wasn't already present.

### Step 4: Regression check

```bash
.venv/bin/python -m pytest tests/test_arc4_goal_resolver.py -q
```
Confirm `test_llm_escalates_only_when_hypotheses_remain_ambiguous` and `test_llm_does_not_escalate_when_hypothesis_is_not_ambiguous` still pass — these use a `RecordingLLMPort` stub returning a well-formed response with a real `goal_id`, so Step 2's guard shouldn't affect them, but confirm directly rather than assuming.

## Verify

```bash
.venv/bin/python -m pytest tests/test_a154_llm_adapter_failure_visibility.py -q
.venv/bin/python -m pytest tests/test_arc4_goal_resolver.py -q
make test-a
make test-all
```

## Files Modified

| File | Change |
|------|--------|
| `arc_runtime/bundle.py` | `SyncLLMPortAdapter.chat()` returns `""` (not `"{}"`) on any failure path; logs a warning at each failure site; new module-level `logger` |
| `agents/arc4/goal_resolver.py` | `_parse_llm_response` requires a truthy `goal_id` before accepting a parsed mapping as a valid patch |
| `tests/test_a154_llm_adapter_failure_visibility.py` | New, 7 tests |

## Risks

- Low risk — this only affects the failure path (previously silently wrong) and adds a required-field check that `plan_generator.py` already uses successfully for its own LLM patch path, so the pattern is proven in this codebase.
- Does not fix the underlying `ollama` connectivity issue in whatever environment produced the original live-smoke evidence — that's an environment/config concern, not something this card can fix from inside the codebase. After this lands, a disconnected `ollama` will show up as WARNING log lines instead of corrupting goal hypotheses, which is the actual deliverable.
