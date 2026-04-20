# A-029 - Restore Full-Suite Pytest Green Without Brain-Internal Imports

## Card metadata

- Card: A029
- Priority: P1
- Layer: ARC runtime tests
- Depends on: A024, A025, A026, A027, A028

## Summary

Triage the 22 `pytest -q` problems captured 2026-04-19 (2 collection errors + 20 failures) into actionable categories without reintroducing `mcp_engine.*` or `sidequests.*` runtime imports. This plan covers the two **in-scope** fixes (G1 collection errors via a stringified annotation, G6-adjacent doc-reference tests) and then spawns one follow-up A-card per remaining category rather than bundling everything.

## Implementation approach

### Phase 0 — Freeze the current baseline

Before starting, capture the exact failure list so follow-up cards can cite it:

```sh
PYTHONPATH=. .venv/bin/python -m pytest -q \
  --ignore=tests/test_b168_graph_exploration.py \
  --ignore=tests/test_b169_kuzu_roles.py \
  2>&1 | tail -40 > .arc/a029-baseline.txt
```

`.arc/` is already gitignored. Keep the file locally as evidence while executing this plan; do not commit it.

### Phase 1 — G1: unblock collection in `entity_graph.py`

Root cause: `agents/arc3/entity_graph.py:47` uses `db: KuzuClient` as a runtime annotation while `KuzuClient` is defined only under `if TYPE_CHECKING:`. Python evaluates default/annotation expressions when the class body runs, so this raises `NameError` at import.

Two valid fixes; pick #1:

1. **Stringify the annotation** (surgical, preserves TYPE_CHECKING seam):
   ```python
   def __init__(self, db: "KuzuClient", task_id: str, llm_client: Any = None):
   ```
   String-form annotations are not evaluated at def time. The `TYPE_CHECKING` import stays exactly as-is, and the production runtime has no new dependency.

2. *(Rejected)* Add `from __future__ import annotations` at the top of the file. This defers all annotations globally, but opt-in PEP 563 has known dataclass-interaction footguns and would be a module-wide behavior change for a one-line bug.

**Do not** import `KuzuClient` at runtime from `mcp_engine.graph.kuzu_client` or similar — that violates the BacklogRules.md rule 4 MCP boundary.

After the fix, verify:
```sh
PYTHONPATH=. .venv/bin/python -c "from agents.arc3 import entity_graph; print('ok')"
PYTHONPATH=. .venv/bin/python -m pytest --collect-only -q tests/test_b168_graph_exploration.py tests/test_b169_kuzu_roles.py
```

The tests themselves may still fail their assertions — that's G3-adjacent drift and belongs to a follow-up card. The only goal here is collection.

### Phase 2 — G1 follow-up card placeholder

If `test_b168_graph_exploration.py` or `test_b169_kuzu_roles.py` fail their assertions after collection succeeds, create a new A-card (`A0xx-b168-b169-restore-kuzu-roles-tests.md`) describing the specific drift. Do not fix those assertions inside A029.

### Phase 3 — Spawn triage cards (one per category)

Write one `backlog/Axxx.md` + `backlog/plans/A-xxx-*.md` pair per group below. Use the A026/A027 format (small, evidence-linked, one category per card). Add each to `masterBacklogTracker.md` in the same commit as the card files.

- **A-xxx (G1-test-assertions)** — b168 / b169 test assertion drift after collection is unblocked. Owner decides whether to realign or mark the tests as pending a real KuzuDB fixture.
- **A-xxx (G2-b-series-drift)** — one *child* card per B-series test file: `test_b176_plateau_explore_untried.py`, `test_b185_failure_taxonomy.py`, `test_b186_trajectory_plan_adherence.py`, `test_b218_replan_branching.py`, `test_b111_ledger.py`. Each follows the A026/A027 pattern: name the A-series change that broke the old expectation, update assertions, no production change. Keep the cards small.
- **A-xxx (G3-runner-fixtures)** — `test_arc3_durable_runner.py` and `test_arc3_harness.py`. Likely requires examining whether the test fixtures are importing legacy in-process SideQuests wrappers that A005/A006 removed; if so, the tests need MCP-seam fixtures instead. Could reveal an A030+ production-side gap if the runner still has a code path that bypasses the seam.
- **A-xxx (G4-solver-drift)** — two `test_arc3_solver.py` tests. Investigate whether A018 cross-chunk plateau memory or A023 probe guard changed the expected state-machine transitions.
- **A-xxx (G5-import-boundary)** — `test_import_boundary.py::test_no_direct_bootstrap_imports_in_production_paths`. Critical to investigate first — this is the guardrail that should have caught the sub-agent's `entity_graph.py` violation. If the test itself is passing-but-wrong-oracle, tighten it; if a real production import slipped in, remove it.
- **A-xxx (G6-offline-bundle)** — the 3 `test_arc_offline_bundle.py` tests that reference `docs/arc3-offline-setup.md`. Decide:
  - Option A: delete all three tests and any manifest entries referencing the doc (if the offline-bundle feature is dormant).
  - Option B: restore a proper `docs/arc3-offline-setup.md` via a dedicated A-card covering its content.
  - Option C: `pytest.skip(reason="pending A0xx offline-bundle decision")` at module scope until A or B is chosen.
- **A-xxx (G6-submission-compliance)** — `test_submission_compliance.py::test_offline_mode_validation`. One-file card in the A026/A027 pattern.

### Phase 4 — Staged migration

As each follow-up card lands:

1. Remove the corresponding `--ignore=` flag from the CI pytest invocation (or from the developer-facing `make test` target, once such a target exists — see Assumptions).
2. Tracker row flips to `complete`.
3. `A029` can be marked complete only when every category has either (a) been fixed on `master`, or (b) been intentionally skipped at module scope with a cited follow-up card number.

### Phase 5 — Guardrail

After all follow-up cards have landed, add to `ARCHITECTURE.md` (or to `BacklogRules.md`): *"No production file under `agents/`, `arc_runtime/`, or `sidequest_mcp_client/` may import `mcp_engine.*` or `sidequests.*` at runtime. This is enforced by `tests/test_import_boundary.py` and must stay green in CI."*

The wording is already present in spirit in `ARCHITECTURE.md` and `BacklogRules.md`; this step just makes it load-bearing by tying it to a green test.

## Concrete file additions/edits (this card only)

- edit `agents/arc3/entity_graph.py:47` — stringify the `KuzuClient` annotation.
- create `backlog/A029.md` (done).
- create `backlog/plans/A-029-full-suite-pytest-green.md` (this file).
- edit `backlog/masterBacklogTracker.md` — add row for A029.
- per Phase 3, create N follow-up A-card stubs with `State: ready` and small scopes. Each is its own commit.

## API/interface changes

None from this card's direct changes. The follow-up cards may surface production gaps (especially G3) that require API work; any such work gets its own card.

## Tests to add or run

After Phase 1:
```sh
PYTHONPATH=. .venv/bin/python -m pytest --collect-only -q 2>&1 | tail -10
```
Expect zero collection errors (down from 2).

After each follow-up card:
```sh
PYTHONPATH=. .venv/bin/python -m pytest -q
```
Failure count monotonically decreases.

Long-term:
```sh
PYTHONPATH=. .venv/bin/python -m pytest -q
```
Expected final state: all pass, zero skips without a cited follow-up card.

## Validation commands

```sh
# Phase 1 exit criterion:
PYTHONPATH=. .venv/bin/python -m pytest --collect-only -q 2>&1 | grep -E "^[0-9]+ tests? collected" | head -1
# Expect: "682 tests collected" with no "errors during collection" line.

# Overall card exit criterion:
PYTHONPATH=. .venv/bin/python -m pytest -q 2>&1 | tail -1
# Expect: "N passed" with N == total, zero failures.

# Boundary guardrail:
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_import_boundary.py
# Expect: passes.
```

## Assumptions/defaults

- The sub-agent's sibling-repo symlink and `entity_graph.py` import hacks are permanently off the table; this plan does not revive them under any alternative framing.
- A per-category follow-up card is cheaper than a single mega-card because each category has a different owner-style decision. Bundling would force an all-or-nothing review.
- `make test` (distinct from the existing `make test-a`) is *not* created by this card. If/when a broader developer-facing target is wanted, it belongs to the tracker cleanup that happens once the follow-up cards land.
- `.arc/a029-baseline.txt` is a local artifact for the executor; git does not track it.
- Phase 3's "one card per B-series file" is a heuristic — if two files share exactly one root cause (e.g., both broke on A023's decision-source rename), collapsing them into one card is fine as long as the collapsed card cites both files explicitly.
