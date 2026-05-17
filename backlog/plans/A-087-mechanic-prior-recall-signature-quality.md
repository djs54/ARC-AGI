# Plan: A-087 — mechanic prior recall signature quality

## Card metadata

- **Card:** A087
- **Priority:** P0
- **Layer:** transport/client seam
- **Depends on:** A075, A081, A084

## Summary

Make aggregate mechanic recall useful by improving the graph-shaped signature ARC sends over MCP and adding tests around matching behavior.

Graph-solution classification: graph retrieval/query quality. Use a labeled property graph signature with stable identifiers and bounded features; avoid full trace or full grid payloads.

## Implementation approach

1. Define a compact mechanic recall signature:
   - action set
   - action cardinality
   - archetype/victory hypothesis
   - recent effect histogram
   - coordinate relevance summary
   - object/terminal progress trend
   - failure signals
   - world-model graph motif summary
2. Add signature builder to ARC runtime.
3. Normalize MCP response diagnostics:
   - store empty
   - no match above threshold
   - match returned
4. Add fixture priors for tests.
5. Verify planner sees recalled priors and A084 diagnostics report the state.

## Concrete file additions/edits

- `agents/arc3/orchestrator.py`
  - Build and send richer recall signatures.
- `agents/arc3/world_model.py`
  - Export bounded motif/effect summaries.
- `sidequest_mcp_client/mcp_brain_client.py`
  - Preserve diagnostic fields from sidequests-brain.
- `tests/test_a087_mechanic_prior_recall_signature_quality.py`
  - Add signature and recall fixture tests.
- Sidequests-brain backlog/card if server-side diagnostics or seed priors are missing.

## API/interface changes

MCP payload shape remains `recall_mechanic_priors(signature=...)`, but `signature` becomes richer and documented:

```json
{
  "action_set": ["ACTION1", "ACTION2", "ACTION3"],
  "archetype": "race",
  "effect_histogram": {"pixel_churn": 18, "object_progress": 5},
  "terminal_trend": "flat"
}
```

## Tests to add or run

```bash
pytest -q tests/test_a087_mechanic_prior_recall_signature_quality.py
pytest -q tests/test_a084_mechanic_memory_transfer_diagnostics.py tests/test_mcp_brain_client.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- ARC should not import sidequests-brain internals.
- If server-side matching needs improvement, create sidequests-brain cards rather than bypassing MCP.
- Signatures must stay compact enough for hot-path telemetry.
