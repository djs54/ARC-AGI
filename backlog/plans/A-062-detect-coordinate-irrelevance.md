# Plan: A-062 — detect when action coordinates are irrelevant

## Card metadata

- **Card:** A062
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A061

## Summary

Teach the agent to test whether parameterized action arguments actually control observed effects. The smoke showed requested `ACTION6` coordinates changed, but the effect location followed an independent sequence. This card adds a causal signal so policy and prompts stop overfitting to irrelevant coordinates.

Graph-solution classification: this is causal evidence modeling. Store only compact `ActionArgumentEvidence` summaries in the memory graph, not every raw frame diff. Use bounded graph paths later to answer “has this action’s coordinate argument mattered in similar states?”

## Implementation approach

1. Capture per-action coordinate/effect samples:
   - action id
   - requested `x`, `y`
   - changed cell coordinates from frame diff
   - number of changed cells
   - frame hash before/after
2. Add coordinate relevance scoring:
   - high relevance if changed cells cluster near requested coordinate or move consistently with requested changes
   - low relevance if requested coordinates vary but effect location follows another path
   - unknown until enough samples exist
3. Persist the learned status in orchestrator state:
   - `args_effective=true|false|unknown`
   - `coordinate_relevance_score`
   - sample count
4. Wire into action selection:
   - if `args_effective=false`, do not choose coordinates through goal-conditioned targeting
   - emit a stable default coordinate or omit coordinate reasoning where API requires dummy values
   - mark rationale as `coordinate_irrelevant_default`
5. Wire into A061 macro mode:
   - macro mode should use default coordinates when arguments are irrelevant
   - macro stop conditions should still inspect environment changes
6. Summarize coordinate causality for graph memory at safe boundaries:
   - action id
   - sample count
   - requested-coordinate variance
   - effect-coordinate variance
   - relevance score
   - args-effective status
   - representative evidence only

## Concrete file additions/edits

- `agents/arc3/grid_analysis.py`
  - Add helper to extract changed-cell coordinates and coordinate/effect distance summaries.
- `agents/arc3/orchestrator.py`
  - Track coordinate relevance per action.
  - Suppress targeted-coordinate rationale when relevance is low.
- `agents/arc3/runner.py`
  - Ensure request coordinates and frame deltas are available to the detector after each ARC API action.
- `tests/test_a062_coordinate_relevance.py`
  - Add fixtures for irrelevant coordinates, relevant click-like coordinates, and insufficient evidence.

## API/interface changes

- No external API changes.
- Add optional trace fields:
  - `args_effective`
  - `coordinate_relevance_score`
  - `coordinate_relevance_samples`
  - `coordinate_relevance_reason`

## Graph-memory model notes

Recommended model: labeled property graph through the existing MCP seam.

Starter provenance shape:

```text
(:Action {action_id})
  -[:HAS_ARGUMENT_EVIDENCE]->
(:ActionArgumentEvidence {
  task_id,
  action_id,
  sample_count,
  relevance_score,
  args_effective,
  reason
})
```

Future retrieval should use bounded traversals keyed by action id, archetype, and compact scene hash. Avoid high-fan-out “all observations for ACTION6” traversals.

## Tests to add or run

Add tests for:

- 2026-04-27 smoke pattern marks `ACTION6.args_effective=false`
- click-like action where requested and changed coordinates align marks `args_effective=true`
- insufficient samples remains `unknown`
- low relevance removes targeted-coordinate rationale
- low relevance does not block macro execution

Validation commands:

```bash
pytest -q tests/test_a062_coordinate_relevance.py tests/test_a061_single_action_macro_executor.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Coordinate relevance is a local online belief and should be reset per puzzle unless strong cross-puzzle memory support exists.
- Low coordinate relevance should not imply the action is useless. It only means coordinate choice is probably not the controlling variable.
