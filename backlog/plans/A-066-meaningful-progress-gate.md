# Plan: A-066 — meaningful progress gate for dense reward loops

## Card metadata

- **Card:** A066
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A058, A063, A065

## Summary

Introduce a central runtime concept: `meaningful_progress`. Dense pixel novelty can remain available as telemetry, but action policy, plateau logic, loop pressure, and hypothesis reinforcement should use meaningful progress instead of raw frame churn.

## Implementation approach

1. Extend progress reward computation:
   - preserve existing `dense_reward`
   - add `progress_class`: `terminal`, `level`, `score`, `object_monotonic`, `goal_distance`, `pixel_churn`, `none`
   - add boolean `meaningful_progress`
2. Define initial gate:
   - true if `env_reward > 0`
   - true if levels completed or score increased
   - true if `terminal_value_score` increased
   - true if object progress is monotonic over a short window and tied to goal/player roles
   - false for isolated low-ratio cell changes with no terminal/object trend
3. Update loop-pressure handling:
   - increment `consecutive_no_progress_steps` when `meaningful_progress` is false
   - do not clear fatigue or blocks on `pixel_churn`
   - keep telemetry reward intact for analysis
4. Update solver plateau scoring:
   - action family evidence should discount `pixel_churn`
   - plateau lock should not be sustained solely by raw frame deltas
5. Update traces:
   - include `meaningful_progress`
   - include `progress_class`
   - include `progress_gate_reason`

## Concrete file additions/edits

- `agents/arc3/runner.py`
  - Update `_compute_progress_reward` to return progress classification.
  - Update no-progress counters and reset conditions.
- `agents/arc3/orchestrator.py`
  - Consume `meaningful_progress` when updating action fatigue, blocks, and hypothesis evidence.
- `agents/arc3/solver.py`
  - Use meaningful progress for plateau and action-family scoring.
- `agents/arc3/failure_taxonomy.py`
  - Ensure pixel-churn loops classify as `stuck_in_loop`.
- `tests/test_a066_meaningful_progress_gate.py`
  - Add focused fixtures.

## API/interface changes

Internal trace/result fields:

- `meaningful_progress: bool`
- `progress_class: str`
- `progress_gate_reason: str`

No external ARC API or MCP protocol changes.

## Tests to add or run

Add tests for:

- one-cell pixel churn does not reset no-progress streak
- env reward always counts as meaningful
- level/score progress counts as meaningful
- monotonic object/goal evidence can count as meaningful
- plateau policy ignores pixel churn when deciding whether to keep exploiting

Validation commands:

```bash
pytest -q tests/test_a066_meaningful_progress_gate.py
pytest -q tests/test_a063_object_progress_scoring.py tests/test_b185_failure_taxonomy.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Keep thresholds conservative: a one-cell change on a 64x64 grid is not meaningful unless independently tied to terminal progress.
- Do not remove dense reward telemetry; only prevent it from driving strategic confidence by itself.
