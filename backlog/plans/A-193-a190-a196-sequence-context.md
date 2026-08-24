# Plan: A193 — Document the A190-A196 Sequence in ARCHITECTURE.md

## Card metadata

- ID: A193
- Priority: P2
- Layer: docs/process
- Dependencies: None

## Summary

Add a new entry to `ARCHITECTURE.md`'s `Implementation Track` section (currently listing the A073-A078 and A118-A123 sequences) documenting A190-A196 as a named sequence, its two-group structure, and its hard ordering constraints — the durable reference for anyone picking up one of these seven cards without having read this session's conversation.

## Technical approach

1. Read `ARCHITECTURE.md`'s `Implementation Track` section (`ARCHITECTURE.md:183-202` as of this plan's writing — confirm current line numbers) in full.
2. Add a new bullet/paragraph after the existing A118-B278 entries, following the section's existing style (short bullets naming each card + a one-line description, as already used for A073-A078):

   ```markdown
   Graph-control-plane compliance hardening and measurement (2026-08-23 graph-engineering
   review, see `backlog/A193.md` for full context and ordering):

   - A190: `book_id` as a first-class `PlanCandidate` field (structural fix, replacing
     six independently-reimplemented metadata resolutions)
   - A191: exclude repeatedly-falsified `book_id`s from the candidate set at construction
   - A192: seed candidate generation from entity-neighborhood graph evidence
     (companion hippocampy tool tracked as B359 in the sibling repo)
   - A194: make termination graph-aware instead of a flat attempt counter
   - A195: assert the Shift-B invariant (no executed candidate is repeatedly-falsified)
     on real run data, with a pass/fail gate script
   - A196: trend Shift-A/Shift-C compliance rates across runs, with a reporting script
   - A197: assert deterministic phases never incur LLM token cost, extending A195's
     compliance_checks.py rather than a second parallel mechanism
   - A198: persist each compliance report to a JSONL history file so rates can be
     trended over time, not just inspected one run at a time

   Ordering: A191 before A195 (the invariant A195 checks is only real once A191 exists);
   A192 and A194 before A196 (two of its four metrics have nothing to report otherwise);
   A195 before A197 (extends the module A195 introduces); A196 before A198 (extends its
   script directly). A190 has no hard dependency on the rest of the sequence.
   ```

3. Do not modify any other section of `ARCHITECTURE.md`. Do not touch any runtime code — if implementation seems to require it, stop and reconsider whether this card's scope is correct.

## Concrete file changes

| File | Change |
|------|--------|
| `ARCHITECTURE.md` | New entry in `Implementation Track` documenting the A190-A196 sequence |

## Tests

None — this is a documentation-only card. No test file is created.

## Validation commands

```bash
grep -n "A190\|A191\|A192\|A193\|A194\|A195\|A196" ARCHITECTURE.md
```

Confirm the new entry renders correctly as markdown (visual check, no automated test needed for a docs-only change).

## Assumptions/defaults

- This card intentionally has no test suite and no acceptance criteria beyond the doc update itself — matching the `docs/process` layer's existing treatment elsewhere in this backlog (see `BacklogRules.md` section 6, "Allowed layers").
- If, during implementation, any of A190/A191/A192/A194/A195/A196 have already landed and their actual scope changed from what's described in their current card/plan files, update this card's summary to match reality rather than the original session's description — this card should describe what the sequence actually is, not what it was originally proposed as.
