# Plan: A-082 — deterministic cheap-probe action path

## Card metadata

- **Card:** A082
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A079, A080

## Summary

Turn `CHEAP_PROBE` into a true deterministic controller path. The controller should select from graph/planner evidence and return a legal action without invoking the mental sandbox or LLM.

Graph-solution classification: this is graph-enabled operational control. Use bounded local evidence from the per-game LPG and prior planner selection; do not run broad graph traversals in the hot path.

## Implementation approach

1. Add a dedicated cheap-probe action builder on `ARCOrchestrator`.
2. Selection order:
   - current `planner_selection.selected`
   - `_last_planner_selection.selected`
   - deterministic fallback from legal `available_actions`
3. Preserve provenance:
   - `decision_source`
   - `planner_candidate_count`
   - `planner_selected_prior_id`
   - `evidence_path`
   - `cheap_probe_reason`
4. Bypass `_mental_sandbox` entirely when the reasoning controller mode is `CHEAP_PROBE`.
5. Emit a `cheap_probe_applied` trace event with enough evidence to audit the bypass.

## Concrete file additions/edits

- `agents/arc3/orchestrator.py`
  - Add deterministic cheap-probe action builder and wire it before prompt/sandbox construction.
- `agents/arc3/reasoning_controller.py`
  - Keep cheap-probe trigger semantics explicit.
- `agents/arc3/world_model_planner.py`
  - Ensure selected candidate has compact evidence-path data.
- `agents/arc3/runner.py`
  - Preserve cheap-probe telemetry in snapshots.
- `tests/test_a082_deterministic_cheap_probe_action_path.py`
  - Add bypass and legal-action tests.

## API/interface changes

No public CLI changes.

Trace additions:

```json
{
  "operation": "cheap_probe_applied",
  "decision_source": "cheap_probe",
  "planner_candidate_count": 1,
  "bypassed_llm": true
}
```

## Tests to add or run

```bash
pytest -q tests/test_a082_deterministic_cheap_probe_action_path.py
pytest -q tests/test_a079_stall_classified_early_stop.py tests/test_a080_world_model_eval_controller_metrics.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- If only one legal action exists, the deterministic fallback may select it with coordinates from the probe policy.
- If multiple legal actions exist and no planner selection exists, prefer untested actions before repeats.
- Cheap-probe should escalate to LLM only when no legal deterministic action can be produced.
