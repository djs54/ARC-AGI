# A228 — Click-Target Coverage Filter Audit: Plan

## Card metadata

- Card: `backlog/A228.md`
- Depends on: A192, A208, A224 Task 2, A226

## Summary

Investigation-first plan, mirroring A209/A212/A215's own structure in this backlog. Three tracks, run in order — later tracks depend on earlier findings.

## Track A: confirm the coverage-filter hypothesis

`coverage` is a `PerceivedEntity.attributes` field computed client-side during `perceive.py`, never persisted to the graph — so it can't be recovered from a past run's graph state after the fact. This track needs a **fresh** live smoke, instrumented to capture it.

### Steps

1. Add a temporary debug log line to `agents/arc4/plan_generator.py::_click_targets` (do not commit this — instrumentation only, remove before any PR): right after `coverage = float(attrs.get("coverage") or 0.0)`, log `entity.attributes.get("entity_ref")`, `coverage`, and whether it was skipped.
2. Run a live smoke:
   ```bash
   export CAMPY_MCP_CMD="../hippocampy/.venv/bin/python -m campy.adapters.mcp_server"
   PYTHONPATH=. .venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 30 2>&1 | tee /tmp/a228_coverage_debug.log
   ```
3. Grep the debug output for any entity where `coverage > 0.5` and confirm at least one real occurrence of the filter actually firing (not just theoretically possible).
4. Separately, query the resulting graph directly (same `MCPBrainClient` + `ArcGraphQueryPort` pattern used throughout A224-A226's own verification) for that run's domain distribution, and cross-reference: does a CONVERGED/COMPLEX entity ever coincide with a coverage-filtered-out entity_ref?

**If Track A finds no real coverage-filter exclusion of a non-DISORDER entity** (e.g., this run's specific puzzle just didn't reproduce it, or entity 3 in the original run turns out to have had low coverage and was excluded for a different reason) — stop here, write the actual mechanism found into the card's Outcome, and do not proceed to Track B/C on a disproven premise.

## Track B: is entity 3's rule directly-actionable, or a bbox-overlap artifact?

Only run if Track A confirms the coverage filter is real. Read `agents/arc4/graph_queries.py::_attribute_entity` (the bbox-overlap mechanism, A176/A218) directly. For the specific CONVERGED entity found in Track A's fresh run, query `fetch_entity_neighborhood(entity_ref)` and inspect the rule's `action_family`/`from_color`/`to_color` fields. Cross-reference against which coordinates were actually clicked when that rule's evidence was recorded (`fetch_entity_history`'s `step` values, matched against `agent_execution_trace.json`'s `action_x`/`action_y` for those same steps).

- If the clicked coordinates match the entity's own bbox: the rule is directly-actionable, strengthening the case that excluding it from candidacy is a real gap.
- If the clicked coordinates are elsewhere: the rule is a bbox-overlap attribution artifact (a passive receiver), and "should this bypass the coverage filter" becomes a different, harder question — bypassing the filter would generate a candidate ("click this entity") whose actual causal claim is about a *different* location, which could itself mislead scoring in a new way. Write this finding into the card's Outcome explicitly either way.

## Track C: decide and implement (or explicitly decide not to)

Based on Track A/B's actual findings, not presupposed:

- **If the filter is a real, direct-action-blocking bug:** design a targeted fix. Leading candidate (stated here as a starting point, not a commitment): `_click_targets` gains an optional `entity_domains: Mapping[Any, CynefinDomain] | None = None` parameter; when provided, an entity with `domain not in (DISORDER,)` skips the `coverage > 0.5` check (graph-confirmed evidence overrides the geometric proxy). Requires threading `entity_domains` from `_build_candidates` (which already computes it per-entity via A226's `classify_entity_domain_detailed`, but currently *after* `_click_targets` has already filtered — this would need a call-order change: classify domains for all visible entities first via `classify_all_entity_domains`, pass the result into `_click_targets`, then continue the existing per-candidate scoring loop). TDD throughout; a new test proving a CONVERGED entity with `coverage > 0.5` is now included must exist alongside a regression test proving a DISORDER entity with `coverage > 0.5` is still excluded (the filter's original purpose — cheaply rejecting whole-background regions with no evidence either way — must survive).
- **If Track B finds the rule is a bbox-overlap artifact, not directly actionable:** the coverage filter is very likely NOT the bug — bypassing it would surface a misleading candidate. Write this conclusion into the Outcome with the supporting evidence, close the card as "audited, no fix warranted," and consider whether a *different* card is needed (e.g., should bbox-overlap-attributed rules be flagged differently from directly-attributed ones in `EntityNeighborhoodClassification`, so scoring can tell them apart in the future) — file that separately if the evidence supports it, don't build it speculatively here.
- **If Track A disproves the premise entirely:** close as "investigated, original hypothesis did not hold," write the real mechanism found instead.

## Validation commands (only if Track C implements a fix)

```bash
.venv/bin/python -m pytest tests/ -k "click_target or a228" -v
make test-a
make test-all
```

Plus a live-smoke + direct graph re-verification, same discipline as A225/A226 — confirm a real CONVERGED/COMPLEX entity actually becomes a candidate post-fix, not just a passing unit test.

## Assumptions/defaults

- None presupposed beyond what's stated as evidence already gathered in `backlog/A228.md`'s Problem section. Track A must re-confirm before Track B/C proceed.
