# Plan: A-083 — explicit early-stop decision telemetry

## Card metadata

- **Card:** A083
- **Priority:** P1
- **Layer:** evaluation/harness
- **Depends on:** A079, A080

## Summary

Emit non-executing world-model decisions as first-class evaluation rows. This keeps executed ARC steps distinct from controller decisions that happen before the next action.

Graph-solution classification: this is graph-enabled observability. Decision rows should cite bounded evidence from the per-game graph without dumping the full graph.

## Implementation approach

1. Add `WorldModelDecisionMetrics` to `benchmarks/arc3/world_model_eval.py`.
2. Add writer support for snapshots with `snapshot_type: world_model_decision`.
3. Emit a decision snapshot when A079 chooses `EARLY_STOP`.
4. Include compact graph evidence:
   - action id
   - effect class
   - repeated frame hash count
   - stall evidence count
   - threshold
   - current graph node/edge counts
5. Update summary counters from both step rows and decision rows.

## Concrete file additions/edits

- `benchmarks/arc3/world_model_eval.py`
  - Add decision row dataclass and summary counters.
- `run_single_puzzle.py`
  - Route decision snapshots to the world-model live stream.
- `agents/arc3/runner.py`
  - Emit early-stop decision snapshot through progress callback.
- `agents/arc3/orchestrator.py`
  - Provide decision evidence payload.
- `tests/test_a083_explicit_early_stop_decision_telemetry.py`
  - Add schema and summary tests.

## API/interface changes

World-model JSONL gains a new row kind:

```json
{
  "kind": "world_model_decision",
  "decision": "early_stop",
  "executed_step_count": 4,
  "decision_step": 5,
  "trigger": "single_action_terminal_stall"
}
```

## Tests to add or run

```bash
pytest -q tests/test_a083_explicit_early_stop_decision_telemetry.py
pytest -q tests/test_a078_world_model_evaluation_harness.py tests/test_a080_world_model_eval_controller_metrics.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Decision rows are compact and do not include full graph snapshots.
- Existing consumers may ignore unknown row kinds.
- The final summary remains one row at run end.
