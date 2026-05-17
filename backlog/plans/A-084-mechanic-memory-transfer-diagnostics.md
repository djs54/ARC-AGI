# Plan: A-084 — mechanic memory transfer diagnostics

## Card metadata

- **Card:** A084
- **Priority:** P0
- **Layer:** transport/client seam
- **Depends on:** A075, A081

## Summary

Expose the aggregate mechanic memory lifecycle as explicit telemetry: recall attempted, recall status, prior count, planner use, and final transfer status.

Graph-solution classification: this is operational diagnostics for a cross-game LPG memory channel. The key graph risk is a dead traversal path where priors exist but never influence planning.

## Implementation approach

1. Normalize recall responses into:
   - `mechanic_prior_recall_status`
   - `mechanic_prior_count`
   - `mechanic_prior_error_code`
2. Track planner use separately:
   - `mechanic_prior_used_count`
   - `planner_selected_prior_id`
   - `planner_selected_prior_source`
3. Define summary state:
   - `capability_missing`
   - `zero_priors`
   - `priors_recalled_not_used`
   - `prior_used`
4. Keep capability missing non-degraded.
5. Add synthetic eval tests and MCP fixture tests.

## Concrete file additions/edits

- `sidequest_mcp_client/mcp_brain_client.py`
  - Normalize aggregate mechanic recall fields.
- `agents/arc3/orchestrator.py`
  - Store last recall status/count and emit in progress snapshots.
- `agents/arc3/world_model_planner.py`
  - Preserve prior provenance on candidates.
- `benchmarks/arc3/world_model_eval.py`
  - Compute transfer diagnostic state.
- `benchmarks/arc3/adapter.py`
  - Preserve the same fields in ledger/offline wrapper paths.
- `tests/test_a084_mechanic_memory_transfer_diagnostics.py`
  - Cover diagnostic states.

## API/interface changes

World-model step rows gain:

```json
{
  "mechanic_prior_recall_status": "ok",
  "mechanic_prior_count": 3,
  "mechanic_prior_used_count": 1,
  "memory_transfer_state": "prior_used"
}
```

## Tests to add or run

```bash
pytest -q tests/test_a084_mechanic_memory_transfer_diagnostics.py
pytest -q tests/test_a081_aggregate_mechanic_memory_transfer.py tests/test_mcp_brain_client.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- All ARC production integration remains through the MCP client seam.
- The aggregate graph remains in sidequests-brain; ARC only consumes normalized responses.
- The diagnostic state is for observability and should not by itself alter policy.
