# Plan: A-080 — world-model evaluation controller metrics contract

## Card metadata

- **Card:** A080
- **Priority:** P0
- **Layer:** evaluation/harness
- **Depends on:** A076, A077, A078

## Summary

Create a narrow, testable metrics contract for world-model eval. The evaluation stream should not infer critical values from missing fields or defaults; the runtime should publish the controller/planner facts it wants evaluated.

Graph-solution classification: this is graph-enabled testing. Metrics should validate bounded graph usefulness: evidence paths, contradiction edges, mechanic prior hits, and decision impact.

## Implementation approach

1. Define a runtime snapshot schema for world-model control metrics:
   - `reasoning_mode`
   - `reasoning_skip_count`
   - `reasoning_escalation_count`
   - `llm_reason_count`
   - `planner_candidate_count`
   - `selected_candidate_has_prediction`
   - `selected_candidate_has_falsification_condition`
   - `contradiction_edge_count`
   - `hypothesis_demotion_count`
   - `mechanic_prior_used_count`
2. Emit those fields from runner/orchestrator snapshots.
3. Update `WorldModelEvaluator` to consume these fields directly.
4. Make summary counters stateful across step rows, with final-result fallback.
5. Add tests that build synthetic step streams and assert summary math.

## Concrete file additions/edits

- `agents/arc3/runner.py`
  - Include controller/planner counters in progress snapshots.
- `agents/arc3/orchestrator.py`
  - Surface graph contradiction/demotion and planner selection evidence.
- `agents/arc3/reasoning_controller.py`
  - Expose cumulative skip/escalation counters.
- `agents/arc3/world_model_planner.py`
  - Expose candidate count and selected-candidate evidence flags.
- `benchmarks/arc3/world_model_eval.py`
  - Remove constant/default summary values where runtime data exists.
- `tests/test_a080_world_model_eval_controller_metrics.py`
  - Add fixture-stream tests.

## API/interface changes

No CLI changes.

World-model JSONL step rows gain fields from the runtime metrics contract. Existing fields remain backward compatible.

## Tests to add or run

```bash
pytest -q tests/test_a080_world_model_eval_controller_metrics.py
pytest -q tests/test_a078_world_model_evaluation_harness.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Missing runtime fields should default to neutral values in eval, but tests should cover the populated contract.
- Do not inspect or import sidequests-brain internals; memory-transfer usage must come through ARC telemetry.
- Evidence paths must be bounded and compact enough for JSONL inspection.
