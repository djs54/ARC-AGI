# Plan: A161 — `fetch_untested_actions` Always Returns Empty

## Context

`agents/arc4/graph_queries.py::_extract_action_ids` (current lines 151-170) is the shared normalizer for `fetch_untested_actions`'s raw MCP response. It only recognizes these container keys: `("actions", "untested_actions", "action_ids", "results", "items")`. The real server response (`campy/brain/thalamus/tools/arc_queries.py::arc_get_untested_actions`, hippocampy repo) is `{"untested": [...], "tested": [...]}` — key `untested`, not in that list — so the method always returns `[]`.

## Implementation Steps

### Step 1: Add the real key

In `agents/arc4/graph_queries.py::_extract_action_ids`, current line ~160:

```python
for key in ("actions", "untested_actions", "action_ids", "results", "items"):
```

becomes:

```python
for key in ("untested", "actions", "untested_actions", "action_ids", "results", "items"):
```

Placed first since it's the confirmed-real key for the only current caller (`fetch_untested_actions`); the rest of the list stays as a fallback for other potential response shapes / other callers of this shared helper (check whether `_extract_action_ids` is used by any other method besides `fetch_untested_actions` — grep first — if so, confirm this doesn't change behavior for that other caller's real response shape).

### Step 2: Tests

New file `tests/test_a161_fetch_untested_actions_key_mismatch.py`. Same stub-`brain_client` pattern as A160's tests (a `call_tool(name, payload)` stub returning canned dicts).

1. `test_real_server_shape_untested_key_recognized` — stub returns `{"untested": ["ACTION5", "ACTION6"], "tested": ["ACTION1", "ACTION2"]}` — assert `fetch_untested_actions()` returns `["ACTION5", "ACTION6"]`.
2. `test_empty_untested_list` — stub returns `{"untested": [], "tested": ["ACTION1"]}` — assert `fetch_untested_actions()` returns `[]` (not an error, just genuinely nothing untested).
3. `test_legacy_actions_key_still_works` — stub returns `{"actions": ["ACTION3"]}` — assert still recognized (regression guard for the pre-existing fallback keys).
4. `test_capability_missing_returns_empty` — stub returns `{"status": "capability_missing"}` — assert `[]`, no exception (regression guard).

## Verify

```bash
.venv/bin/python -m pytest tests/test_a161_fetch_untested_actions_key_mismatch.py -v
make test-a
make test-all
```

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/graph_queries.py` | `_extract_action_ids` adds `"untested"` to its container-key list |
| `tests/test_a161_fetch_untested_actions_key_mismatch.py` | New, 4 tests |

## Risks

- Very low — single key addition to a fallback chain, all existing keys/behavior preserved.
