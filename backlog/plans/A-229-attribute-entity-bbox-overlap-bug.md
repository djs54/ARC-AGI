# A229 — `_attribute_entity` Bbox-Overlap Misattribution: Plan

## Card metadata

- Card: `backlog/A229.md`
- Depends on: A176/A218 (`_attribute_entity`'s original bbox-overlap design and its no-op fallback), A228 (where this was found)

## Summary

`agents/arc4/graph_queries.py::_attribute_entity` (lines 621-638) attributes a step's changed cells to whichever entity's bbox contains the most of them, unnormalized by bbox size and without checking whether the click itself landed inside that entity's bbox. A228's Track B found direct live evidence (`ar25-0c556536`, 2026-08-31) of this misattributing effects to a large background-sized entity (`entity_ref=0`, bbox `(0, 0, 62, 29)`) across 3/3 observed real transitions, in every case where the actual click coordinate fell *outside* that entity's own bbox column range. This card investigates the scope of the bug and implements a targeted fix.

## Track A: confirm scope (does this generalize beyond A228's single data point?)

`_attribute_entity` is puzzle-agnostic, so the bug is structural (any entity whose bbox covers a large fraction of the grid will absorb credit for scattered changes elsewhere), but confirm with at least one more live run before assuming the exact fix shape:

1. Reuse A228's instrumentation pattern (temporary debug log in `record_transition`, not committed) logging click `(x, y)`, `attributed_entity_ref`, changed-cell `(row, col)` list, and the attributed entity's own bbox.
2. Run one fresh live smoke (budget: 1-2 runs, this is a live-rate-limited resource — see A228's own environment notes for `CAMPY_MCP_CMD`/`.venv` worktree setup).
3. For every logged transition, check whether the click coordinate fell inside the attributed entity's own bbox. Tally the false-attribution rate (click outside bbox but still attributed) vs. true-attribution rate (click inside bbox, attributed correctly).
4. If the false-attribution rate is near-zero this run (i.e., A228's finding was itself an outlier), narrow the fix's urgency and re-scope Track B/C accordingly — don't force a fix onto a rare edge case without saying so.

## Track B: design and implement the fix

Two candidate directions to evaluate, not commitments — pick based on what's simplest and least likely to regress `record_transition`/`record_rule_evidence`'s existing callers:

**Option 1 — require click-inside-bbox as a precondition.** `_attribute_entity` (or its caller) additionally receives the click coordinate (`execution.candidate.payload`'s `x`/`y`, already read elsewhere via `_targeted_entity_ref`) and only attributes to an entity whose bbox contains that click coordinate. Among entities whose bbox contains the click, keep the existing "most changed cells" tiebreak. If no entity's bbox contains the click, fall back to `_targeted_entity_ref` (A218's existing "we know what was clicked" signal) rather than the current no-op-only fallback.

**Option 2 — normalize by bbox area.** Instead of raw `count`, score `count / bbox_area` (or similar), so a tiny bbox catching most of a small localized change outranks a huge bbox catching a small fraction of a large one. Simpler diff, but doesn't address A228's exact failure mode as directly (Option 1 is a closer fit to the actual evidence: click landed fully outside the attributed entity's bbox, not just "a smaller fraction of it").

Prefer Option 1 unless Track A's fresh data suggests otherwise — it directly targets the failure mode actually observed (click-outside-bbox misattribution), and reuses A218's existing `_targeted_entity_ref` machinery instead of adding a new concept.

### TDD

- New test: a synthetic case where a large "background" entity's bbox contains most changed cells, but the click coordinate is outside its bbox and inside a second, smaller entity's bbox — assert attribution goes to the smaller entity (or `_targeted_entity_ref`'s target if no entity's bbox contains the click).
- Regression test: the existing "biggest overlap wins" behavior when the click *does* land inside the attributed entity's bbox must be unchanged (don't regress the legitimate case this heuristic was built for).
- Regression test: `_targeted_entity_ref`'s no-op fallback path (A218) must be unaffected.

## Track C: live-verify

Fresh live smoke + the same instrumented-then-removed debug log pattern, confirming a previously-misattributed large entity no longer absorbs credit for an out-of-bbox click. Also re-run `classify_entity_domain` against the resulting graph state for the affected entity and confirm its domain classification changes appropriately (e.g., a previously-COMPLEX large entity may drop to fewer/no live rules once misattributed evidence stops accumulating).

## Validation commands

```bash
.venv/bin/python -m pytest tests/ -k "attribute_entity or a229" -v
make test-a
make test-all
```

Plus a live-smoke + direct graph re-verification, same discipline as A225/A226/A228.

## Assumptions/defaults

- None presupposed beyond what A228's Outcome documents as already-gathered evidence. Track A must re-confirm scope before Track B/C proceed, same investigation-first discipline as A228.
