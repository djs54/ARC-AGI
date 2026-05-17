# Plan: A-081 — aggregate mechanic memory transfer verification

## Card metadata

- **Card:** A081
- **Priority:** P0
- **Layer:** transport/client seam
- **Depends on:** A075, A078

## Summary

Verify the full aggregate mechanic memory loop over the MCP seam: per-game graph summary publication, aggregate recall, planner/controller use, and world-model eval reporting.

Graph-solution classification: this is operational verification for a cross-game LPG memory. The key risk is a shadow or dead graph channel: data exists but never influences a bounded decision path.

## Implementation approach

1. Extend MCP fixture tests to expose:
   - `publish_mechanic_summary`
   - `recall_mechanic_priors`
2. Ensure `MCPBrainClient` returns normalized telemetry:
   - `status`
   - `results`
   - `prior_count`
   - `tool_name`
   - `memory_degraded`
3. Ensure ledger/adapters preserve memory-transfer fields.
4. Add planner evidence-path fields:
   - `mechanic_prior_id`
   - `mechanic_prior_confidence`
   - `mechanic_prior_source`
5. Update `WorldModelEvaluator`:
   - set `memory_transfer_active` only when a prior is recalled and used
   - keep capability-missing as neutral, not degraded
6. Add two fixture paths:
   - missing-tool fixture stays clean
   - available-tool fixture produces active transfer

## Concrete file additions/edits

- `sidequest_mcp_client/mcp_brain_client.py`
  - Normalize aggregate mechanic memory tool responses.
- `benchmarks/arc3/adapter.py`
  - Preserve ledger fields in offline/benchmark wrapper paths.
- `agents/arc3/orchestrator.py`
  - Record prior recall/use in trace snapshots.
- `agents/arc3/world_model_planner.py`
  - Attach prior provenance to candidates.
- `benchmarks/arc3/world_model_eval.py`
  - Compute `memory_transfer_active` from actual prior use.
- `tests/test_a081_aggregate_mechanic_memory_transfer.py`
  - Add MCP fixture tests for missing and available aggregate memory tools.

## API/interface changes

No public CLI changes.

Telemetry additions:

```json
{
  "mechanic_prior_used_count": 1,
  "memory_transfer_active": true,
  "planner_candidates": [
    {
      "action_id": "ACTION6",
      "mechanic_prior_id": "mechanic:single_action_tick",
      "evidence_path": ["Game", "Mechanic", "PlanTemplate"]
    }
  ]
}
```

## Tests to add or run

```bash
pytest -q tests/test_a081_aggregate_mechanic_memory_transfer.py
pytest -q tests/test_mcp_brain_client.py tests/test_a075_aggregate_mechanic_memory.py
pytest -q tests/test_a078_world_model_evaluation_harness.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- ARC_AGI should not reach into sidequests-brain internals; all verification uses MCP fixtures or the client contract.
- Treat KuzuDB/sidequests-brain as the aggregate graph source of truth.
- Avoid global `Action` or `Mechanic` supernode traversals in planner paths; use bounded signature matching and confidence filters.
