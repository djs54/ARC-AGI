# A245 — Goal-Domain Classification: Plan

## Card metadata

- Card: `backlog/A245.md`
- Depends on: A217/A218/A220/A224/A226 (entity-scoped Cynefin classification this extends), A243 (Track B's own live 0/0 finding — the load-bearing precondition), A233 (the "confirm data exists before building the consumer" discipline), A246 (companion finding, already merged — different mechanism, no hard code dependency)

## Design — investigation-first, three sequential steps, do not skip ahead

### Step 1 (mandatory, do this first, in full, before touching any implementation code)

Trace the real server-side write path for `fetch_goal_evidence`'s `contradicts`/`supports` fields. This data is served by the sibling `hippocampy` repo (the MCP graph server), not this repo — read there, do not guess from this repo's client-side code alone.

Concretely:
1. Find the MCP tool handler in `hippocampy` that answers `fetch_goal_evidence` (likely under `campy/brain/thalamus/tools/arc_queries.py` or similar — grep for `fetch_goal_evidence`, `contradicts`, `supports`, `update_goal_confidence`, `record_evaluation`).
2. Trace what actually calls `update_goal_confidence` (or whatever writes the contradicts/supports counters) — find the real gating condition. A243's own investigation (see A243's Outcome in `backlog/masterBacklogTracker.md`) found this is very likely gated on `meaningful_progress`/`has_meaningful_progress`, the same narrow signal A214/A240 characterized elsewhere — confirm or refute this directly by reading the code, cite exact file:line.
3. If you have time/access, cross-check against a fresh live-smoke run's real telemetry (see "Live-verify" below) rather than relying on A243's already-cited evidence alone — but the code read is the primary, mandatory evidence; live data is corroborating, not a substitute.
4. Write your verdict directly into `backlog/A245.md`'s Problem/Outcome sections with exact file:line citations. This step alone is worth completing and documenting even if it changes what Step 2/3 should be.

### Step 2 (conditional — only if Step 1 finds the data is genuinely reachable)

If real, non-zero `contradicts`/`supports` values are actually reachable (not just theoretically possible, but confirmed to fire in real gameplay): design and implement `classify_goal_domain_detailed` (name it appropriately once you've seen the real evidence shape), mirroring `classify_entity_domain_detailed`'s DISORDER/CONVERGED/COMPLEX/CHAOTIC classification logic (read that function first — it's in `agents/arc4/annatar_signals.py`, find it via grep). Wire it into `compute_cycle_signals`'s `domain` field computation, but ONLY for goal-type anchors — entity-type anchors must be completely unaffected (verify with regression tests, not just by code inspection).

Check whether `fetch_goal_evidence`'s per-goal record is already available inside `compute_cycle_signals`'s call site this same cycle (A233's own precedent found `_merge_graph_evidence` already fetches this — check whether that's reachable from here without an extra round trip, or whether a new fetch is required).

### Step 3 (conditional — same gate as Step 2)

If Step 2 is built: extend A217's existing `complex_domain_deepening_multiplier` patience logic in `transition()` so it's reachable for goal-type anchors for the first time. Write a regression test that proves this was unreachable before your change (mock/fake a goal-type anchor with COMPLEX-classified evidence under the OLD code and show the multiplier never applied) and reachable after.

### If Step 1 finds the data is NOT reachable (structurally inert)

Do not build Step 2/3. Instead:
- Document the precise verdict in `backlog/A245.md`'s Outcome section, with exact file:line citations for the gating condition found.
- If a specific, narrow prerequisite fix is identifiable (e.g., "loosen X gate the same way A240 added a parallel signal rather than loosening the strict one"), name it precisely as a NEW backlog card stub — do not implement that fix yourself, it is out of scope for A245 (per the card's own "Explicitly NOT this card's job"). Just describe it precisely enough that a future card could pick it up. Do not create the new card file yourself unless the investigation is airtight and you have time — a clearly-written note in A245's Outcome is sufficient; creating the follow-up card is optional, not required for A245 to be complete.
- Mark A245's acceptance criteria for Step 2/3 as N/A with the reasoning, not unchecked — the investigation itself, done rigorously, is a complete and valid outcome for this card (same standard as A233 Track B and A240, both of which closed as "no fix built, verdict documented" and were treated as fully complete cards).

## Implementation approach (only relevant if Step 2/3 are reached)

### Files (if built)

- Modify: `agents/arc4/annatar_signals.py` — new `classify_goal_domain_detailed` (or equivalent), wired into `compute_cycle_signals`.
- Test: new `tests/test_a245_goal_domain_classification.py`.

### TDD (if built)

- New test: a goal-type anchor with real, non-zero `contradicts`/`supports` evidence in a shape that should classify as CONVERGED — confirm `domain` reflects it.
- New test: same for CHAOTIC (contradicting evidence dominant).
- New test: same for COMPLEX (genuinely mixed/disagreeing evidence) — and confirm `complex_domain_deepening_multiplier` is now reachable (the regression-proof test described in Step 3 above).
- New test: a goal-type anchor with `0`/`0` evidence (the common case per A243) — confirm `domain` still defaults to DISORDER, same as today, and the existing cycle-budget fallback still concludes in a comparable number of cycles (no hang).
- New test: an entity-type anchor — confirm `classify_entity_domain_detailed`'s existing behavior is completely untouched (the critical regression guard).
- Regression: every existing `tests/test_a217_*.py`/`test_a218_*.py`/`test_a220_*.py`/`test_a224_*.py`/`test_a226_*.py` test continues to pass unchanged.

### Validation commands

```bash
.venv/bin/python -m pytest tests/test_a245_goal_domain_classification.py -v   # if built
make test-a
make test-all
```

### Live-verify

Same environment/discipline as every prior card this investigation (`.venv` worktree symlink if isolated, `CAMPY_MCP_CMD` absolute path, `campy start` + warm-up wait if the daemon shows offline, full `tee`'d output read completely, generous timeout — recent runs have taken 2-4+ minutes). If Step 2/3 built: run a live smoke and look for a goal-type anchor's `domain` field resolving to something other than DISORDER at least once, and confirm `ADVANCE`/`DEEPENING` behavior visibly reflects it. If Step 1 alone: a live smoke run's real telemetry can still serve as corroborating evidence for the reachability verdict (e.g., scanning `artifacts/agent_execution_trace.json` for every `contradicts`/`supports` value across a full run, the same way A243 did) — do this if time allows, it strengthens the verdict, but the code-read trace is the primary requirement either way.

## Assumptions/defaults

- Investigation-first, per the card's own explicit instruction — do not begin Step 2/3 implementation until Step 1's verdict is written down with real evidence.
- If genuinely ambiguous after a real, careful code read (not just "I couldn't find it quickly"), default to treating the data as NOT reachable and document precisely what you checked and why it was inconclusive — do not build speculative machinery on an assumption.
