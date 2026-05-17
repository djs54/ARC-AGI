# Plan: A-079 — stall-classified early stop and cheap probe policy

## Card metadata

- **Card:** A079
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A073, A074, A076, A078

## Summary

Use the per-game labeled property graph as the policy evidence source for single-action stalls. The controller should treat repeated unproductive action-effect edges as a classified failure mode, not as another reason to ask the LLM what to do.

Graph-solution classification: this is an operational LPG control problem. The useful query is bounded neighborhood evidence around the current `(:Action)-[:CAUSED]->(:Effect)` pattern, not global graph search.

## Implementation approach

1. Add an explicit stall-policy result to the reasoning controller:
   - `llm_reason`
   - `cheap_probe`
   - `early_stop`
   - `reclassify_mechanic`
2. Base the decision on bounded evidence:
   - consecutive `single_action_terminal_stall` claims
   - repeated frame hashes
   - no terminal improvement trend
   - no object-progress claim
   - action set cardinality
3. Add delayed-reward guardrails:
   - require a minimum probe count before `early_stop`
   - reset stall counters on new object/terminal evidence
   - do not early-stop if a mechanic prior predicts delayed unlock and the predicted effect is still untested
4. Wire the decision into the runner/orchestrator loop so it changes behavior, not only telemetry.
5. Emit trace fields consumed by A078:
   - `stall_policy`
   - `stall_evidence_count`
   - `stall_threshold`
   - `reasoning_mode`

## Concrete file additions/edits

- `agents/arc3/reasoning_controller.py`
  - Add stall-policy classification and thresholds.
- `agents/arc3/orchestrator.py`
  - Feed compiled world-model claims and mechanic-prior hints into the controller.
- `agents/arc3/runner.py`
  - Honor `cheap_probe` and `early_stop` outcomes in the live loop.
- `benchmarks/arc3/world_model_eval.py`
  - Include stall-policy fields in step rows and summary.
- `tests/test_a079_stall_classified_early_stop.py`
  - Add deterministic fixtures for stall, delayed reward, and reset-on-new-evidence.

## API/interface changes

No public CLI changes.

Internal payload addition:

```json
{
  "reasoning_mode": "early_stop",
  "stall_policy": "single_action_terminal_stall",
  "stall_evidence_count": 6,
  "stall_threshold": 5
}
```

## Tests to add or run

```bash
pytest -q tests/test_a079_stall_classified_early_stop.py
pytest -q tests/test_a076_runtime_behavior.py tests/test_a078_world_model_evaluation_harness.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Start with conservative thresholds to avoid aborting delayed-reward mechanics.
- The per-game graph remains bounded; do not add global traversals in the hot path.
- Aggregate mechanic priors may delay early stop only when they provide a specific untested predicted effect.
