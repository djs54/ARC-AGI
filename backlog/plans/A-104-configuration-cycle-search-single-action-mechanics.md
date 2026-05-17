# Plan: A-104 — Configuration cycle search for single-action mechanics

## Card metadata

- **Card:** A104
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A079, A093, A101, A103

## Summary

Add a bounded controller mode for single-action games where the action advances a finite graph configuration cycle.

## Implementation approach

1. Extend `ReasoningMode` with `CONFIGURATION_CYCLE_SEARCH`.
2. Track cycle state in `ReasoningController`:
   - seen configuration hashes
   - first seen step
   - goal-alignment score per configuration
   - level count per configuration
   - max cycle probes
3. Use A103 transformation evidence:
   - enter cycle mode when one legal action repeatedly changes `configuration_hash` or `transform_class=configuration_cycle_step`.
   - do not rely on pixel novelty alone.
4. Stop conditions:
   - current configuration hash repeats and no active goal hypothesis improved.
   - max cycle probes reached.
   - prediction falsification count exceeds threshold.
5. Continue conditions:
   - new configuration hash.
   - goal alignment improves.
   - level count / terminal state changes.
6. Emit first-class decision rows:
   - `configuration_cycle_search`
   - `configuration_cycle_closed_unsolved`
   - `configuration_goal_alignment_improved`
7. Ensure final result maps closed-unsolved to `failure_class="strategy_exhausted"` with `failure_reason="configuration_cycle_closed_unsolved"`.

## Concrete file additions/edits

- Edit `agents/arc3/reasoning_controller.py`
- Edit `agents/arc3/world_model.py`
- Edit `agents/arc3/world_model_planner.py`
- Edit `agents/arc3/orchestrator.py`
- Edit `benchmarks/arc3/world_model_eval.py`
- Add `tests/test_a104_configuration_cycle_search.py`

## API/interface changes

`WorldModelGraph` should add:

```python
def get_configuration_cycle_evidence(self, action_id: str, limit: int = 32) -> dict: ...
```

World-model decision rows should add:

```json
{
  "world_model_decision": "configuration_cycle_closed_unsolved",
  "configuration_hash": "cfg-...",
  "configuration_repeat_count": 2,
  "goal_alignment_delta": 0.0
}
```

## Tests to add or run

```bash
.venv/bin/python -m pytest -q tests/test_a104_configuration_cycle_search.py tests/test_a079_stall_classified_early_stop.py tests/test_a093_fast_prediction_falsification_action_quarantine.py
.venv/bin/python -m pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Default max cycle probes: `min(12, max_steps_remaining)`.
- A single improvement in terminal level/state exits cycle mode and lets normal solve continue.
- Cycle search is graph-local and does not introduce MCP hot-path reads.
