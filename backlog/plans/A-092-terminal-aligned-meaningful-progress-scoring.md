# Plan: A-092 — terminal-aligned meaningful progress scoring

## Card metadata

- **Card:** A092
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A073, A074, A089, A090

## Summary

Treat meaningful progress as a graph-backed relation between action, object effect, and terminal/goal movement. Local object deltas should be useful observations, not enough by themselves to justify exploit mode.

## Implementation approach

1. Add a terminal-alignment classifier for object progress: `terminal_aligned`, `local_only`, `regressing`, `oscillating`, or `delayed_effect_pending`.
2. Extend compiler claims with `terminal_alignment` and bounded evidence path IDs.
3. Update reward/progress metadata so `meaningful_progress=true` requires terminal alignment or an explicit delayed-effect guard.
4. Update policy override scoring to demote local-only progress after terminal regression.
5. Emit telemetry fields in step snapshots.

## Concrete file additions/edits

- `agents/arc3/grid_analysis.py`
- `agents/arc3/world_model_compiler.py`
- `agents/arc3/orchestrator.py`
- `agents/arc3/runner.py`
- `tests/test_a092_terminal_aligned_meaningful_progress.py`

## API/interface changes

```json
{
  "terminal_alignment": "local_only",
  "terminal_alignment_reason": "object moved but goal distance oscillated",
  "terminal_alignment_evidence_path": ["action-...", "effect-...", "obs-..."]
}
```

## Tests to add or run

```bash
pytest -q tests/test_a092_terminal_aligned_meaningful_progress.py
pytest -q tests/test_a089_graph_backed_planner_prediction_edges.py tests/test_a090_mechanic_prior_use_planner_ranking.py
make test-a
```

## Assumptions/defaults

- Terminal distance can be noisy; use a short rolling window rather than single-frame judgment.
- Do not discard object progress. Store it as local graph evidence even when it is not meaningful solve progress.
