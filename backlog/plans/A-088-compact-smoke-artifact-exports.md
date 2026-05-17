# Plan: A-088 — compact smoke artifact exports

## Card metadata

- **Card:** A088
- **Priority:** P1
- **Layer:** evaluation/harness
- **Depends on:** A078, A080, A084

## Summary

Stop duplicating every large trace and graph payload inside `submission_results_single.json`. Keep detailed artifacts in their dedicated files and make the final result a compact index plus summary.

Graph-solution classification: graph-enabled evaluation/output design. Full graph snapshots should not be embedded by default; export bounded summaries and artifact references.

## Implementation approach

1. Add compact export mode for live smoke final results.
2. Move large fields to dedicated artifacts or truncate/summarize:
   - `agent_execution_trace`
   - `master_timeline`
   - full `world_model_snapshot.nodes`
   - full `world_model_snapshot.edges`
   - repeated prompt traces
3. Preserve high-signal summaries:
   - node/edge counts
   - contradiction/demotion counts
   - recent effect histogram
   - memory transfer state
   - planner/gating counters
4. Add artifact path references.
5. Add synthetic large-result tests with size assertions.

## Concrete file additions/edits

- `run_single_puzzle.py`
  - Compact final export option/default for smoke mode.
- `agents/arc3/runner.py`
  - Produce compact result payload or summary fields.
- `benchmarks/arc3/world_model_eval.py`
  - Provide summary fields for final export.
- `tests/test_a088_compact_smoke_artifact_exports.py`
  - Add synthetic large artifact tests.

## API/interface changes

Possible final result shape:

```json
{
  "failure_class": "strategy_exhausted",
  "steps": 30,
  "world_model_summary": {
    "node_count": 123,
    "edge_count": 120,
    "memory_transfer_state": "zero_priors"
  },
  "artifacts": {
    "agent_execution_trace": "agent_execution_trace.json",
    "world_model_live": "submission_results_single.world_model.live.jsonl"
  }
}
```

## Tests to add or run

```bash
pytest -q tests/test_a088_compact_smoke_artifact_exports.py
pytest -q tests/test_a078_world_model_evaluation_harness.py tests/test_a080_world_model_eval_controller_metrics.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Do not remove detailed artifacts; only stop duplicating them in the final result.
- Keep submission/server payload compatibility separate from diagnostic exports.
- A configurable escape hatch may allow full export for deep debugging.
