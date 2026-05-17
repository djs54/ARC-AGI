# Plan: A-094 — multi-action churn exhaustion world-model decision

## Card metadata

- **Card:** A094
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A085, A092, A093

## Summary

Convert broad churn evidence into a graph-backed decision. If the world model has tested the action surface and found only churn, harm, or local-only progress, the controller should stop spending action budget on undirected probing.

## Implementation approach

1. Extend all-action churn evidence with terminal-aligned progress counts.
2. Add `multi_action_churn_exhausted` decision in `ReasoningController`.
3. Create a compact graph evidence path from action-effect summaries to the decision.
4. Wire orchestrator handling for early stop, mechanic reclassification, or optional reset/replan.
5. Update world-model eval summary counters.

## Concrete file additions/edits

- `agents/arc3/reasoning_controller.py`
- `agents/arc3/world_model.py`
- `agents/arc3/orchestrator.py`
- `benchmarks/arc3/world_model_eval.py`
- `tests/test_a094_multi_action_churn_exhaustion_decision.py`

## API/interface changes

```json
{
  "kind": "world_model_decision",
  "decision": "early_stop",
  "world_model_decision": "multi_action_churn_exhausted",
  "all_actions_churn_detected": true,
  "failure_reason": "all legal actions produced churn/harm/local-only progress"
}
```

## Tests to add or run

```bash
pytest -q tests/test_a094_multi_action_churn_exhaustion_decision.py
pytest -q tests/test_a085_multi_action_no_progress_gate.py tests/test_a093_fast_prediction_falsification_action_quarantine.py
make test-a
```

## Assumptions/defaults

- Require at least two observations for each legal action unless the action is already harmful.
- Do not trigger exhaustion if a mechanic prior with compatible evidence predicts delayed progress.
- Prefer explicit decision telemetry over silent step-budget termination.
