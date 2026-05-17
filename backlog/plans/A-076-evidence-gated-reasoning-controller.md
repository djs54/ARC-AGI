# Plan: A-076 — evidence-gated reasoning controller

## Card metadata

- **Card:** A076
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A074, A075

## Summary

Implement the controller that decides when expensive reasoning is justified. The controller should use graph-backed evidence deltas, not raw step count, as the main trigger.

Graph-solution classification: this is graph-enabled system control. The graph is useful because escalation depends on relationships: prediction contradicted by observation, action caused only churn, mechanic prior predicts a recovery, hypothesis has no remaining experiment.

## Implementation approach

1. Create `agents/arc3/reasoning_controller.py`.
2. Define:
   - `ReasoningDecision`
   - `ReasoningMode`: `cheap_execute`, `compile_only`, `retrieve_priors`, `llm_reason`, `cheap_probe_batch`, `early_stop`
   - `ReasoningTrigger`
3. Inputs:
   - `WorldModelSummary`
   - `CompiledWorldDelta`
   - active/demoted hypotheses
   - action-effect table
   - mechanic-prior availability
   - phase and budget state
4. Decision rules:
   - If no material world-model delta and cheap action remains, choose `cheap_execute`.
   - If only one action is legal and repeated trials show no meaningful terminal/object progress, choose `cheap_probe_batch` or `early_stop`.
   - If a prediction is contradicted, choose `llm_reason`.
   - If mechanic priors are stale or missing and phase allows memory, choose `retrieve_priors`.
   - If mechanic prior gives a concrete plan template, choose `llm_reason` with that compact prior.
   - If all hypotheses are demoted and no experiments remain, choose `early_stop` with specific failure class.
5. Emit structured trace:
   - `reasoning_decision`
   - `reasoning_trigger`
   - `reasoning_skipped_reason`
   - `world_model_delta_hash`
   - `estimated_token_saved`
6. Integrate with orchestrator/runner:
   - call controller before LLM-heavy phases
   - preserve existing behavior behind feature flag if needed

## Concrete file additions/edits

- `agents/arc3/reasoning_controller.py`
  - New controller and decision dataclasses.
- `agents/arc3/orchestrator.py`
  - Route phase transitions through controller.
- `agents/arc3/runner.py`
  - Track reasoning skip/escalation counters and failure class.
- `agents/arc3/solver.py`
  - Accept controller-provided compact context.
- `tests/test_a076_evidence_gated_reasoning_controller.py`
  - New focused tests.

## API/interface changes

Internal API:

```python
decision = controller.decide(
    world_summary=...,
    compiled_delta=...,
    budget_state=...,
    phase=...,
)
```

Trace additions:

- `reasoning_mode`
- `reasoning_trigger`
- `reasoning_skip_count`
- `llm_reason_count`
- `cheap_execute_count`
- `cheap_probe_batch_count`

## Key risks

- Over-gating can suppress useful reasoning. Mitigate with tests that force escalation on contradiction/new mechanic prior.
- Under-gating recreates the old loop. Mitigate with ACTION6 single-action stall fixture.
- World-model delta hashing must be stable enough to identify unchanged belief state without hiding real changes.

## Tests to add or run

Add tests for:

- unchanged world model skips LLM reasoning
- contradiction triggers LLM reasoning
- mechanic prior triggers reasoning with compact context
- single-action stall avoids repeated full reasoning cycles
- controller emits trace reasons
- memory phase policy is preserved

Validation commands:

```bash
pytest -q tests/test_a076_evidence_gated_reasoning_controller.py
pytest -q tests/test_a074_world_model_compiler.py tests/test_a075_aggregate_mechanic_memory.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Start with deterministic rules and thresholds.
- Add a feature flag if integration risk is high, but default test fixtures should exercise the new path.
