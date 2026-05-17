# Plan: A-090 — mechanic prior use in planner ranking

## Card metadata

- **Card:** A090
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A075, A081, A084, A087, A089

## Summary

Use aggregate mechanic priors as graph-shaped transfer evidence in planner ranking. This remains a labeled-property-graph problem: a prior should match by action pattern, effect pattern, failure mode, and current local causal evidence, not by raw text similarity alone.

## Implementation approach

1. Normalize recalled priors into bounded planner hints:
   - mechanic id/source
   - action pattern
   - expected effect pattern
   - failure/recovery policy
   - confidence
2. Add a local graph compatibility score:
   - action exists and is legal
   - current effect histogram matches prior effect pattern
   - failure signals match prior recovery policy
   - contradictions reduce confidence
3. Update planner ranking:
   - boost candidates with compatible priors and graph-backed predictions
   - demote candidates contradicted by current per-game graph
   - never select an illegal action due to a prior
4. Attach prior provenance to selected candidates.
5. Update eval telemetry to count only selected-prior influence as `prior_used`.

## Concrete file additions/edits

- `agents/arc3/world_model_planner.py`
  - Add prior normalization, compatibility scoring, and ranking integration.
- `agents/arc3/world_model.py`
  - Add compact compatibility query helpers if needed.
- `agents/arc3/orchestrator.py`
  - Preserve selected prior fields in step snapshots.
- `benchmarks/arc3/world_model_eval.py`
  - Ensure `memory_transfer_state=prior_used` requires selected candidate provenance.
- `tests/test_a090_mechanic_prior_use_planner_ranking.py`
  - Add planner fixture tests.

## API/interface changes

Selected candidate provenance:

```json
{
  "action_id": "ACTION4",
  "mechanic_prior_id": "mechanic:single-action-unlock",
  "mechanic_prior_source": "aggregate_memory",
  "prior_compatibility_score": 0.68,
  "evidence_path": ["mechanic:single-action-unlock", "effect-pattern:unlock", "action-task-ACTION4"]
}
```

## Tests to add or run

```bash
pytest -q tests/test_a090_mechanic_prior_use_planner_ranking.py
pytest -q tests/test_a081_aggregate_mechanic_memory_transfer.py tests/test_a084_mechanic_memory_transfer_diagnostics.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Prior transfer must be evidence-gated; recalled memory is a candidate signal, not authority.
- Keep traversal bounded and filter by legal actions before ranking.
- Missing or malformed prior payloads degrade to ordinary planner behavior.
