# A254 — Readiness Probe Semantic-Search False Negative: Plan

## Card metadata

- Card: `backlog/A254.md`
- Depends on: none — self-contained single-file fix

## Corrected approach (mid-implementation revision)

The plan below was written under the card's original diagnosis (probe content
is semantically empty). Mid-implementation, independent investigation
confirmed a deeper root cause: hippocampy's `upsert_lesson` write path
(`lessons.create_lesson`, a SPARQL-templated `NamedQuery`) never reaches
`OxigraphClient.write_node()` — the only code path that populates the
sqlite-vec index — so `recall_relevant_lessons` cannot find *any*
freshly-written Lesson today, regardless of probe content. See
`backlog/A254.md`'s "Corrected root cause" section for the full trace
through `campy/brain/hippocampus/graph/gateway.py` and `oxigraph_client.py`.

**Consequence for this plan:** Step 2 below (richer probe text) was
implemented anyway as harmless hygiene, but it is not the fix. The actual
fix is Step 2': stop gating pass/fail on `recall_relevant_lessons` at all —
treat `upsert_lesson`'s own real `lesson_id` as sufficient proof of
persistence (the write is synchronous, under the daemon's graph-store lock,
before the response returns), and demote `recall_relevant_lessons` to a
best-effort, non-fatal diagnostic call.

## Design (confirmed by direct read before writing this plan)

- `sidequest_mcp_client/readiness.py:135-241` — `check_mcp_readiness`, the full function; the `require_roundtrip_persistence` branch (`~191-241`) is what this card modifies.
- `sidequest_mcp_client/readiness.py:~125-132` — `_response_contains_probe(readback, probe_token)`, the helper that scans a `recall_relevant_lessons` response for the probe token substring — still used for the best-effort diagnostic path.
- `sidequest_mcp_client/mcp_brain_client.py:~592,622` — existing client wrappers around `upsert_lesson`/similar tools, useful reference for the real argument shapes this codebase already uses elsewhere.
- `arc_runtime/runner_shell.py:166` — the one call site: `check_mcp_readiness(..., require_roundtrip_persistence=True, ...)`.
- (Corrected-root-cause investigation, sibling repo, read-only) `hippocampy/campy/brain/thalamus/tools/lessons.py:540-` (`upsert_lesson`), `hippocampy/campy/brain/hippocampus/graph/queries/lessons.py:672-` (`lessons.create_lesson` `NamedQuery`, `sparql=` field), `hippocampy/campy/brain/hippocampus/graph/gateway.py:378-385` (`_dispatch_oxigraph`, routes any `sparql=`-bearing query straight to `execute_write`, bypassing the vector-store special cases), `hippocampy/campy/brain/hippocampus/graph/oxigraph_client.py:842-870` (`write_node`, the only method that calls `vector_store.upsert_vector()`/`index_text()`) and `:968-988` (`execute`/`execute_write`, the path `lessons.create_lesson` actually takes — never calls `write_node()`).
- No exact-match/ID-based lesson lookup tool exists on the current MCP tool surface (checked `hippocampy/campy/brain/thalamus/tool_schemas.py`'s full tool list) — so the card's "Step 1" exact-match-tool option isn't available; `explore_graph`'s `start_node_id` param is graph-traversal shaped, not a clean fit, and wasn't used.

### Step 1 — check for an exact-match tool first

Done: no exact-match lesson-lookup tool exists on hippocampy's current MCP
surface (`tool_schemas.py` has no `get_lesson`/`fetch_lesson_by_id`-shaped
tool). Rather than inventing one or forcing `explore_graph` into this role,
this plan instead treats `upsert_lesson`'s own synchronous write-then-respond
behavior as the exact-match-equivalent proof of persistence — no separate
read call needed for the pass/fail gate at all.

### Step 2 — probe text hygiene (kept, not the fix)

```python
probe_token = f"arc_readiness_probe_{uuid.uuid4().hex[:12]}"
probe_text = f"Readiness probe: verifying MCP write-read persistence for session {probe_token}."
write_payload = session.call_tool(
    "upsert_lesson",
    {
        "domain": "readiness_probe",
        "text": probe_text,
        "valence": 0.5,
        "confidence": 0.9,
        "tags": ["readiness_probe", "arc"],
    },
    timeout=call_timeout,
)
```

Implemented as-is. Marginal hygiene improvement; does not by itself fix the
live failure (see corrected root cause above) since the embedding index is
never populated for this write regardless of text content.

### Step 2' — the actual fix: stop gating on semantic recall

```python
lesson_id = write_payload.get("lesson_id") or write_payload.get("id")
if lesson_id in (None, "", "None"):
    raise ReadinessError(...)  # unchanged: still the hard failure mode

# recall_relevant_lessons is now best-effort only:
if "recall_relevant_lessons" in tool_names:
    try:
        readback = session.call_tool("recall_relevant_lessons", {...}, timeout=call_timeout)
    except Exception as exc:
        _logger.warning(...)  # never fatal
    else:
        if _is_daemon_offline_response(readback):
            raise ReadinessError(...)  # still fatal -- a genuinely different failure
        if not _response_contains_probe(readback, probe_token):
            _logger.warning(...)  # known B418 gap, not fatal
```

`required_roundtrip_tools` also shrinks from `{"upsert_lesson",
"recall_relevant_lessons"}` to `{"upsert_lesson"}` — the recall tool is no
longer a hard prerequisite for this probe (though it's still required
elsewhere via `runner_shell.py`'s own `required_tools` list, unaffected by
this change).

### Step 3 — retry window

Moot under the corrected fix: there is no retry loop gating pass/fail
anymore (the single best-effort recall call isn't retried either, since
retrying a call that's known to structurally return nothing regardless of
timing would just be theater). If hippocampy's B418 fix later restores real
embedding-based recall and a genuine timing race turns up in practice, that
would be new evidence for a future card, not something to guess at now.

## Implementation approach

### Files

- Modified: `sidequest_mcp_client/readiness.py` — the `require_roundtrip_persistence` branch.
- Modified (not a new file — see rationale in `backlog/A254.md`'s Outcome): `tests/test_readiness.py`.

### TDD

- `test_readiness_roundtrip_success` — unchanged, still green (real write + real recall match).
- `test_readiness_roundtrip_fails_when_not_persisted` → re-purposed as `test_readiness_roundtrip_passes_despite_empty_semantic_recall` — the same fake-server fixture (real `lesson_id`, empty recall) now proves the check *passes*, since that's exactly the false-negative this card fixes. Also asserts the A254 warning is logged.
- New: `test_readiness_roundtrip_fails_when_write_returns_no_lesson_id` — the critical regression guard: a write that never nominally succeeds still fails loudly.
- New: `test_readiness_roundtrip_fails_when_recall_reports_daemon_offline` — proves the best-effort downgrade doesn't swallow a genuinely different real failure.
- Regression: all pre-existing `readiness.py` tests (`current_truth` probe, required-tools check, brain-socket checks) confirmed still green.

### Validation commands (actually run)

```bash
python3 -m pytest tests/test_readiness.py -q          # 13 passed
python3 -m pytest tests/test_observability.py tests/test_trace_durability.py \
  tests/test_import_boundary.py tests/test_a140_cycle_policy.py \
  tests/test_a222_shift_a_static_boundary.py -q       # test-a equivalent, 21 passed
python3 -m pytest tests/ -q                            # test-all equivalent, 1212 passed, 4 skipped
```

(`make test-a`/`make test-all` themselves failed locally on `.venv/bin/python: No such file or directory` — no project venv provisioned in this worktree; ran the equivalent `pytest` invocations directly against the Makefile's own target definitions instead, output above.)

### Live-verify (actually done)

A local hippocampy daemon was already running (`brain_daemon.py` process,
live socket at `~/.campy/brain.sock`) and reachable via `CAMPY_MCP_CMD`
pointed at `hippocampy/.venv/bin/python -m campy.adapters.mcp_server`.
Called `check_mcp_readiness(...)` directly with the exact flags
`runner_shell.py` uses (`require_brain_socket=True,
probe_memory_backend=True, require_roundtrip_persistence=True`) against
this real daemon:

- Result: `True` (previously: `RuntimeError`).
- Logged exactly the expected A254 warning, with a real `lesson_id` and an
  empty `recall_relevant_lessons` readback — independently reconfirming the
  corrected root cause live, not just via source-reading.

## Assumptions/defaults

- Superseded: "default to Step 2 (semantic-content fix) unless Step 1 finds
  an exact-match tool" — Step 1 confirmed no exact-match tool exists, and
  Step 2 alone was confirmed (via source-reading and a live daemon call) not
  to fix the actual failure. The real fix is Step 2' above.
- Kept: the check's actual persistence guarantee stays intact — this is
  still a false-negative fix, not a "make the check pass unconditionally"
  fix. A write that doesn't nominally succeed, or a best-effort recall call
  that reports the daemon is genuinely offline, both still fail loudly.
