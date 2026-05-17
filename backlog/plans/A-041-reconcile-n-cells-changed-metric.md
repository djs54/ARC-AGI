# Plan: A-041 — reconcile `n_cells_changed` metric

## Card metadata

- **Card:** A041
- **Priority:** P1
- **Layer:** evaluation/harness
- **Depends on:** A038

## Summary

Resolve the mismatch where `n_cells_changed` appears zero while action-effect telemetry reports large transformations.

## Implementation approach

1. Trace computation sites for:
   - `perceive_step_response.n_cells_changed`
   - `action_effect_written.n_cells_changed/effect_class`
2. Verify frame inputs and ordering used in each path.
3. Unify to a single frame-diff helper or explicitly split semantics with clear naming.
4. Update serialization fields and docs/comments to avoid analyst confusion.
5. Add regression tests with known non-trivial frame changes.

## Concrete file edits

- `agents/arc3/runner.py`
- `agents/arc3/solver.py`
- `sidequest_mcp_client/observability.py` (if trace field names change)
- `tests/test_arc3_durable_runner.py`
- `tests/test_b182_enhanced_metrics.py` (if metric semantics touch score logic)

## API / interface changes

- Potential internal trace-field rename if semantics are intentionally distinct.
- Keep consumer compatibility by providing transitional mapping if necessary.

## Tests to run

- `pytest -q tests/test_arc3_durable_runner.py tests/test_b182_enhanced_metrics.py`
- `python run_single_puzzle.py --num-puzzles 1 --max-steps 20 --card-id delta_metric_verify`

## Validation commands

- `rg -n "n_cells_changed|large_transformation|action_effect_written|perceive_step_response" agent_execution_trace.json submission_results_single.live.jsonl`

## Assumptions / defaults

- One authoritative frame-diff source is preferable to parallel independent counters.
- If dual metrics are needed, names must encode semantics clearly.
