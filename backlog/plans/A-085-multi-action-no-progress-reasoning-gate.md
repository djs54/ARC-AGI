# Plan: A-085 — multi-action no-progress reasoning gate

## Card metadata

- **Card:** A085
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A079, A080, A082

## Summary

Extend the reasoning controller from single-action stalls to multi-action no-progress patterns. The latest smoke shows 24 no-progress steps in a five-action environment while every step still used `llm_reason`.

Graph-solution classification: graph-enabled operational control. Use bounded per-game LPG evidence around recent `Action -> Effect` observations; do not perform global traversals in the hot path.

## Implementation approach

1. Track per-action progress evidence:
   - tested count
   - recent effect classes
   - meaningful progress count
   - repeated frame hashes
   - last object/terminal progress step
2. Add controller modes/triggers:
   - `multi_action_churn_probe`
   - `multi_action_reclassify`
   - `multi_action_strategy_exhausted`
3. Gate full LLM reasoning when:
   - enough actions have been tested,
   - recent effects are churn/no-op,
   - no meaningful progress was observed in the cooldown window.
4. Reset or relax gating when object/terminal progress appears.
5. Emit metrics:
   - `multi_action_churn_detected`
   - `actions_tested_count`
   - `productive_action_count`
   - `reasoning_mode`

## Concrete file additions/edits

- `agents/arc3/reasoning_controller.py`
  - Add multi-action churn policy.
- `agents/arc3/orchestrator.py`
  - Feed per-action world-model evidence into controller.
- `agents/arc3/runner.py`
  - Preserve policy metrics in live snapshots.
- `benchmarks/arc3/world_model_eval.py`
  - Include multi-action gate signals in step/summary rows.
- `tests/test_a085_multi_action_no_progress_gate.py`
  - Add fixture traces for churn, progress reset, and untested-action guardrail.

## API/interface changes

No public CLI changes.

Telemetry additions:

```json
{
  "reasoning_mode": "cheap_probe",
  "trigger": "multi_action_churn",
  "actions_tested_count": 5,
  "productive_action_count": 0
}
```

## Tests to add or run

```bash
pytest -q tests/test_a085_multi_action_no_progress_gate.py
pytest -q tests/test_a079_stall_classified_early_stop.py tests/test_a082_deterministic_cheap_probe_action_path.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Start conservatively: do not gate until at least three distinct actions or all legal actions have evidence.
- Any object/terminal progress resets the no-progress gate cooldown.
- The gate should choose cheap experiments, not blindly terminate.
