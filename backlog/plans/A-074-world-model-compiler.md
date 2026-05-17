# Plan: A-074 — world-model compiler from step telemetry

## Card metadata

- **Card:** A074
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A066, A070, A073

## Summary

Create a compiler that transforms raw ARC step telemetry into graph facts and belief updates. This is the part that closes the key ARC-AGI-3 gap: local effects must become a global world model.

Graph-solution classification: this is graph implementation work. The output should be LPG-style nodes and edges with bounded traversal semantics. The compiler should filter early and expand late: it should write concise causal facts, not dump every pixel delta into graph memory.

## Implementation approach

1. Create `agents/arc3/world_model_compiler.py`.
2. Define:
   - `CompiledClaim`
   - `ActionEffectClaim`
   - `RelevanceClaim`
   - `ContradictionClaim`
   - `StallClaim`
   - `WorldModelCompiler`
3. Inputs:
   - current step index
   - previous/current frame hash
   - executed action and args
   - available actions
   - progress reward result from A066
   - terminal trend from A070
   - object-progress evidence from A063
   - hypothesis predictions from A065/A073
4. Outputs:
   - graph mutations against `WorldModelGraph`
   - compact `compiled_world_delta` for trace
   - optional failure/route signal such as `single_action_terminal_stall`
5. Implement classification rules:
   - `no_op`: frame unchanged, reward unchanged, no object/terminal progress
   - `pixel_churn`: frame changed but no meaningful/object/terminal progress
   - `object_progress`: object progress positive and not terminal-regressing
   - `terminal_progress`: terminal score improves monotonically
   - `harmful`: terminal trend regressing or state enters losing/dead state
   - `cycle`: repeated frame hash after same action sequence
   - `single_action_terminal_stall`: only one legal action, repeated non-meaningful trials, terminal trend flat/regressing
6. Update graph edges:
   - action caused effect
   - observation supports or contradicts hypothesis
   - action has terminal relevance or lacks terminal relevance
   - coordinate relevance supported/contradicted
7. Add hard caps:
   - only compile recent window for prompt summary
   - aggregate repeated identical claims into counts
   - do not create high-degree color/action hubs without task/session scoping

## Concrete file additions/edits

- `agents/arc3/world_model_compiler.py`
  - New compiler and claim dataclasses.
- `agents/arc3/world_model.py`
  - Add helper methods to apply compiled claims.
- `agents/arc3/orchestrator.py`
  - Call compiler after evaluate/observe boundaries, not per raw pixel operation.
- `agents/arc3/runner.py`
  - Include `compiled_world_delta`, `world_model_failure_signal`, and stall classification in progress logs.
- `benchmarks/arc3/trajectory_eval.py`
  - Add optional scoring fields for compiled claim quality.
- `tests/test_a074_world_model_compiler.py`
  - New deterministic fixture tests.

## API/interface changes

- Internal API:
  - `WorldModelCompiler.compile_step(...) -> CompiledWorldDelta`
  - `WorldModelGraph.apply_compiled_delta(delta)`
- Optional trace fields:
  - `compiled_world_delta`
  - `action_effect_class`
  - `terminal_relevance`
  - `coordinate_relevance`
  - `single_action_terminal_stall`

## Starter traversal/query

Example bounded traversal for deciding whether an action has become non-useful:

```text
Start at current Game -> current Action -> CAUSED Effect edges from last N trials.
Count meaningful terminal/object effects versus churn/no-op/cycle effects.
Return non-useful when churn/no-op/cycle dominates and no recent support edge reaches the active goal hypothesis.
```

Keep traversal task-scoped and window-scoped to avoid action-id supernodes.

## Tests to add or run

Add tests for:

- no-op classification
- pixel-churn classification
- monotonic terminal-progress classification
- contradiction emitted when prediction fails
- single-action ACTION6 terminal-stall fixture
- repeated frame hash cycle detection
- no MCP/LLM calls during compile

Validation commands:

```bash
pytest -q tests/test_a074_world_model_compiler.py
pytest -q tests/test_a066_meaningful_progress_gate.py tests/test_a070_monotonic_terminal_progress_scorer.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- The compiler should be deterministic and cheap.
- LLM reasoning may consume compiler output later, but compiler execution itself must not call the LLM.
- Repeated identical claims should be aggregated with counts and last-seen step.
