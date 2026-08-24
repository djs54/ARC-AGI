# Plan: A206 — Document the A200-A205 Sequence in ARCHITECTURE.md

## Card metadata

- ID: A206
- Priority: P1
- Layer: docs/process
- Dependencies: None

## Summary

Add a new entry to `ARCHITECTURE.md`'s `Implementation Track` section for the trajectory Reasoner family (A200-A205), matching the format already used for A073-A078, A118-A123, and A190-A198.

## Technical approach

1. Read `ARCHITECTURE.md`'s `Implementation Track` section in full, current state (confirm line numbers — it was edited twice already this session, for A190-A198 and again for the Temporal deprecation note).
2. Add a new entry after the A190-A198 sequence's text, following its exact style:

   ```markdown
   Trajectory Reasoner (2026-08-23, see docs/superpowers/specs/2026-08-23-trajectory-reasoner-design.md
   for the full design and backlog/A206.md for sequencing context):

   - A200: pure investigation-thread state machine (no graph/LLM/I/O)
   - A201: hippocampy handoff doc + graph client stubs for investigation threads
   - A202: wire the Reasoner into WorkflowOrchestrator
   - A203: anchor-biasing in goal_resolver.py/plan_generator.py
   - A204: resume/crash-safety -- write-ahead cycles, real-observation reconciliation (P0)
   - A205: degraded-mode fallback + AWAITING_LLM failure handling

   Ordering: A200 and A201 are the only safe parallel step (no file overlap, no
   dependency on each other). A202 depends on both. A203 depends on A202. A204
   and A205 both depend on A202 and have no logical dependency on each other,// but
   are sequenced (A204 then A205) rather than run in parallel because both
   plausibly touch the same files (types.py, workflow.py's Reasoner hook) --
   same file-conflict-safety reasoning as A196/A197 in the prior sequence.
   ```

   Note: fix the stray `//` typo in the sketch above before writing the real file — it's a copy-paste artifact from this plan's own drafting, not intentional.

3. Do not modify any other section of `ARCHITECTURE.md`.

## Concrete file changes

| File | Change |
|------|--------|
| `ARCHITECTURE.md` | New entry in `Implementation Track` for A200-A205 |

## Tests

None — documentation-only card, matching A193's precedent.

## Validation commands

```bash
grep -n "A200\|A201\|A202\|A203\|A204\|A205\|A206" ARCHITECTURE.md
```

## Assumptions/defaults

- Matches A193's precedent exactly: docs-only, no test suite, no code changes. If implementation of this card seems to require touching runtime code, that's a sign the card's scope has drifted.
