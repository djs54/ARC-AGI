# A233 — Goal Resolver Grounding + Evidence Scoping: Plan

## Card metadata

- Card: `backlog/A233.md`
- Depends on: A224, A171 (companion finding, same audit)

## Summary

Two Shift-C findings from a deep audit of `agents/arc4/goal_resolver.py`: (1) `_apply_grounding_gate` is entirely local-state, never consults the graph despite its name; (2) `_merge_graph_evidence`'s goal-gated enrichment only ever sees the top-ranked hypothesis, not the full candidate list. Both need investigation into affordability/impact before committing to an exact fix shape — this plan sets up that investigation and the TDD implementation once a direction is chosen.

## Track A: `_apply_grounding_gate` — investigate before implementing

1. Re-read `_apply_grounding_gate` (lines 435-471) and `_observed_progress` (473-478) in their current exact form — confirm the analysis in `backlog/A233.md` still matches.
2. Identify what graph signal would actually be useful here. Leading candidate: `fetch_rules_for_action`/`fetch_per_action_evidence` for whatever action(s) are associated with `state.active_goal` (via `_goal_action_id`-style extraction, mirroring `graph_queries.py::fetch_goal_evidence`'s own existing helper) — now trustworthy post-A232. Confirm this data is actually available/meaningful at the point `_apply_grounding_gate` runs (before `plan`/`vet`/`execute` for this cycle — the active goal may not yet have an action chosen for *this* cycle, only from prior cycles).
3. Decide: does a graph check *replace* the local grid-hash comparison, or *supplement* it? The local check is cheap and catches a real case (visible grid change) the graph check might not directly address. Write the reasoning into the plan before implementing, not just the code.
4. Estimate round-trip cost: `_apply_grounding_gate` runs every cycle a goal is already active. An extra graph call every cycle needs to be worth it — check whether existing evidence (already fetched earlier in the same `resolve()` call, e.g. via `_merge_graph_evidence`) can be reused instead of a fresh fetch, to avoid doubling graph traffic for this phase.

## Track B: `_merge_graph_evidence` — investigate before implementing

1. Read `graph_queries.py::fetch_goal_evidence`'s full body again (lines 111-151) to confirm exactly which sub-calls are `goal`-gated (`_action_patterns`, `_infer_archetype`, `_goal_action_id`) versus goal-agnostic.
2. Decide the fix shape:
   - **Option A**: call `fetch_goal_evidence` once per top-N hypothesis (not just hypotheses[0]) — more round-trips, but reuses the exact existing mechanism unchanged. Check whether `N=3` (matching A171's own top-3 pattern) is an affordable number of extra calls per cycle.
   - **Option B**: change `fetch_goal_evidence`'s signature to accept a list of goals instead of one, restructuring its internal `action_patterns`/`archetype`/`action_evidence` gating to consider all of them in a single round-trip. Bigger change, touches the `GraphQueryPort` protocol (`ports.py`) and its real implementation — more invasive but avoids the round-trip multiplication of Option A.
   - Prefer Option A unless Track B's own investigation finds a clear reason Option B is needed (e.g., round-trip cost turns out to matter more than expected) — smaller, more localized change, doesn't touch the protocol.
3. Write the decision and reasoning into `backlog/A233.md`'s Outcome before implementing.

## Implementation (once Track A/B decisions are made)

TDD throughout. New tests needed (exact shape depends on Track A/B decisions, but at minimum):
- `_apply_grounding_gate`: a case where the grid hash is unchanged (local check alone would clamp) but real graph evidence exists suggesting the goal is still worth pursuing (or not) — confirm the graph signal is actually consulted and changes the outcome from what local-state-only behavior would have produced.
- `_merge_graph_evidence`: a case where hypothesis #2 (not #1) has real graph-confirmable evidence available — confirm it now receives the same enrichment hypothesis #1 already got, previously it wouldn't have.
- Regression: existing `tests/test_arc4_goal_resolver.py` (and any A224-era tests touching `_apply_grounding_gate`/`_merge_graph_evidence`) pass unchanged unless a stated, reasoned behavior change is documented.

```bash
.venv/bin/python -m pytest tests/test_arc4_goal_resolver.py -v
make test-a
make test-all
```

## Live-verify

Same environment/discipline as every prior card in this investigation. Run a live smoke, confirm via direct graph query + trace inspection that the change actually altered goal-resolution behavior in a real episode (which hypothesis won, or how a stalled goal's confidence behaved differently than pre-fix) — not just a passing unit test.

## Assumptions/defaults

- If Track A concludes a graph check isn't affordable or doesn't change outcomes meaningfully, document that as a legitimate "stated tradeoff, no fix needed" conclusion in the Outcome — same standard A228 used when its own hypothesis didn't pan out as expected.
