# Plan: A-068 — execute-phase memory read firewall and ledger compliance

## Card metadata

- **Card:** A068
- **Priority:** P0
- **Layer:** transport/client seam
- **Depends on:** A064

## Summary

Close the remaining memory/firewall gap exposed by the live smoke. Execute-phase memory reads must be cache-only or skipped, and the ledger must represent those calls as compliant firewall behavior rather than phase violations.

Graph-solution classification: this is graph workload governance and adapter correctness. Graph memory is a good fit for hypothesis/provenance recall, but execute phase must not perform fresh traversals. Use bounded cache lookups during execute and reserve graph expansion for hypothesize/model/route/replan.

## Implementation approach

1. Identify execute-phase memory call sites:
   - `recall_lessons`
   - `analogical_search`
   - `recall_scene_graph_priors`
   - any hybrid matcher graph-memory reads
2. Enforce cache-only execute policy:
   - if cache hit, return cached data with `memory_firewall_action=cached`
   - if miss, return skipped/degraded metadata with no blocking call
3. Update ledger recording:
   - preserve firewall fields in `sidequests_ledger`
   - mark skipped/cached execute memory reads as policy-compliant
   - do not count firewall-skipped calls as phase violations
4. Extend `LedgerBrainClient` wrappers:
   - add `recall_lessons`
   - add `recall_scene_graph_priors`
   - update `analogical_search` to tolerate text and vector signatures
   - keep signature filtering for old mocks
5. Keep graph query constraints explicit:
   - no execute-phase fan-out
   - route/model graph queries must include bounded limits and stable entry filters

## Concrete file additions/edits

- `sidequest_mcp_client/mcp_brain_client.py`
  - Harden execute read policy and return metadata shape.
- `benchmarks/arc3/adapter.py`
  - Add wrapper methods and signature-tolerant forwarding.
  - Preserve firewall metadata in ledger.
- `agents/arc3/orchestrator.py`
  - Ensure phase sync is set before any execute-phase helper consults memory.
- `benchmarks/arc3/trajectory_eval.py`
  - Treat `memory_firewall_action=skipped|cached` as compliant in execute.
- `tests/test_a068_execute_memory_read_firewall.py`
  - New regression tests for violations and wrapper method coverage.

## API/interface changes

Internal wrapper/API additions:

- `LedgerBrainClient.recall_lessons(...)`
- `LedgerBrainClient.recall_scene_graph_priors(...)`
- vector-tolerant `LedgerBrainClient.analogical_search(...)`

No direct production imports of graph internals.

## Graph model/query notes

Recommended policy:

- execute: only lookup exact cached key by task/action/scene hash
- hypothesize/model/route/replan: bounded graph recall by task id, archetype, action id, scene hash, and role ids
- avoid broad action-only hubs such as all `ACTION5` lessons
- include provenance edge metadata: phase, source, cache status, and reason

Regression fixtures should assert that graph-memory wrappers preserve typed payloads for:

- lesson recall
- vector analogical search
- scene graph priors

## Tests to add or run

Add tests for:

- execute cache miss returns firewall skipped without a real memory call
- execute cache hit returns cached data and is compliant
- ledger/eval does not flag skipped/cached execute memory reads
- wrapper exposes `recall_lessons`
- wrapper supports `analogical_search(vector=...)`
- wrapper exposes `recall_scene_graph_priors`
- `notify_turn` wrapper tolerates mocks without `async_dispatch`

Validation commands:

```bash
pytest -q tests/test_a068_execute_memory_read_firewall.py
pytest -q tests/test_a064_memory_firewall.py tests/test_mcp_brain_client.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Firewall-skipped execute reads are expected behavior, not degraded failure, when the action policy already has enough local context.
- Cache-only execute reads should be small, stable, and keyed by explicit task/action/scene inputs.
