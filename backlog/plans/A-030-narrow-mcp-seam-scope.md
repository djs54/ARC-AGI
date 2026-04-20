# A-030 - Narrow MCP-Seam Policy Scope To Runtime Paths

## Card metadata

- Card: A030
- Priority: P1
- Layer: architecture / test boundary
- Depends on: A029

## Summary

Narrow `tests/test_import_boundary.py::PROD_PATHS` to the interactive-runtime paths only. Document the exemption for `benchmarks/arc3/` in ARCHITECTURE.md, BacklogRules.md, CLAUDE.md, AGENTS.md, and GEMINI.md. No production refactoring; no BAD_REGEXES weakening.

## Implementation approach

### 1. Narrow PROD_PATHS

In `tests/test_import_boundary.py`:

```python
# A030: the MCP stdio seam policy applies to the interactive runtime only.
# `benchmarks/arc3/` is offline scoring / submission packaging that embeds
# the brain directly (submission-pack deployment model) and is intentionally
# exempt from this guard. Do not re-add `benchmarks` here without a card.
PROD_PATHS = [
    ROOT / "agents",
    ROOT / "run_single_puzzle.py",
    ROOT / "sidequest_mcp_client",
]
```

Do not touch `BAD_REGEXES` — the pattern set stays exactly as committed.

### 2. Update ARCHITECTURE.md

In the `### MCP v1 — stdio-only production seam` section (around line 63), after the existing paragraph that names the seam contract, add:

```
The MCP stdio seam applies to the interactive runtime path — `agents/arc3/`,
`run_single_puzzle.py`, and `sidequest_mcp_client/`. Offline scoring and
submission packaging under `benchmarks/arc3/` embed the brain directly
(Kuzu client, schema init, loop queue, centroids) and are exempt from the
seam policy, because submission packages cannot depend on a running MCP
subprocess. The import-boundary test (`tests/test_import_boundary.py`)
enforces the runtime scope; benchmarks are not in its PROD_PATHS list.
```

### 3. Tighten BacklogRules.md rule 4

In `backlog/BacklogRules.md` rule 4, change
```
4. No direct SideQuests imports: Production code MUST NOT import SideQuests internals …
```
to
```
4. No direct SideQuests imports (runtime scope): Runtime production code
   under `agents/`, `arc_runtime/`, `run_single_puzzle.py`, and
   `sidequest_mcp_client/` MUST NOT import SideQuests internals or rely on
   migrated SideQuests process files. Offline scoring and submission
   packaging under `benchmarks/arc3/` are exempt (A030) because they embed
   the brain directly. SideQuests artifacts are archive-only; integrations
   must occur via documented MCP-client interfaces or approved shims.
```

### 4. Update the three agent-pointer files

In `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, bullet 1 currently reads:

> 1. **MCP seam only.** Production code under `agents/`, `arc_runtime/`, and `sidequest_mcp_client/` must not import `mcp_engine.*` or `sidequests.*` at runtime …

Change to:

> 1. **MCP seam only (runtime scope).** Runtime production code under `agents/`, `arc_runtime/`, `run_single_puzzle.py`, and `sidequest_mcp_client/` must not import `mcp_engine.*` or `sidequests.*`. `benchmarks/arc3/` is offline packaging and exempt (A030). `tests/test_import_boundary.py` enforces the runtime scope.

Apply the same change to all three files so they stay in sync.

### 5. Verify

```sh
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_import_boundary.py
PYTHONPATH=. .venv/bin/python -m pytest -q  # count failures — A030 drops one
make test-a
```

Expected:
- import-boundary test passes
- failure count drops from 20 to 19 (assuming no other card has landed concurrently)
- `make test-a` still 18/18

## Concrete file additions/edits

- edit `tests/test_import_boundary.py` — narrow `PROD_PATHS`, add comment
- edit `ARCHITECTURE.md` — add scope paragraph under MCP v1 section
- edit `backlog/BacklogRules.md` — tighten rule 4
- edit `CLAUDE.md`, `AGENTS.md`, `GEMINI.md` — update bullet 1
- edit `backlog/masterBacklogTracker.md` — add A030 row
- create `backlog/A030.md` (done)
- create `backlog/plans/A-030-narrow-mcp-seam-scope.md` (this file)

## API/interface changes

None.

## Tests to add or run

- `pytest -q tests/test_import_boundary.py` — must pass
- `make test-a` — must stay 18/18

## Validation commands

```sh
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_import_boundary.py
# expect: 1 passed

make test-a
# expect: 18 passed

# scope sanity: benchmarks should still have brain imports intact
grep -c "mcp_engine" benchmarks/arc3/submission.py
# expect: non-zero (unchanged by this card)
```

## Assumptions/defaults

- Submission packaging keeps its embedded brain model for the foreseeable future. If/when a future card migrates submission to the MCP seam, that card can re-add `benchmarks/` to `PROD_PATHS` as part of the cut-over and verify the test turns green on the new architecture.
- `arc_runtime/` is included in the documentation's "runtime scope" list even though it isn't in `PROD_PATHS` — the directory may not exist yet in every checkout but is reserved by the A002/A005 docs. Keeping it in the doc list matches the policy intent.
