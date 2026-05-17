# Plan: A-100 — World-model eval stream parity for route decisions

## Card metadata

- **Card:** A100
- **Priority:** P1
- **Layer:** evaluation/harness
- **Depends on:** A078, A092, A094

## Summary

Make `submission_results_single.world_model.live.jsonl` carry the same high-signal route/control evidence that currently requires opening the broader live stream.

## Implementation approach

1. Extend `WorldModelStepMetrics` with reward-gate fields.
2. Add terminal trend, goal distance, terminal value score, terminal alignment, and terminal-aligned boolean.
3. Copy all-action churn evidence into step and decision rows.
4. Add a regression test using a CD82-like local-object-progress snapshot.

## Concrete file additions/edits

- `benchmarks/arc3/world_model_eval.py`
- `tests/test_a078_world_model_evaluation_harness.py`

## API/interface changes

World-model step rows add:

```json
{
  "terminal_progress_trend": "oscillating",
  "terminal_goal_distance": 42.5,
  "terminal_alignment": "oscillating",
  "meaningful_progress": false,
  "progress_class": "local_object_progress",
  "all_actions_churn_evidence": {}
}
```

## Tests to add or run

```bash
.venv/bin/python -m pytest -q tests/test_a078_world_model_evaluation_harness.py tests/test_a088_compact_smoke_artifact_exports.py
make test-a
```

## Assumptions/defaults

- This card changes evaluation/harness output only.
- The additional JSONL fields are compact scalar/summary fields, not full graph dumps.
