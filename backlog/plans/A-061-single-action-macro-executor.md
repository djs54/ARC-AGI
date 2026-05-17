# Plan: A-061 — single-action macro executor for deterministic progress

## Card metadata

- **Card:** A061
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A059, A060

## Summary

Add a bounded macro executor for one-action deterministic-progress states. The live smoke showed `ACTION6` was the only available action and caused a consistent one-cell transformation, but the system spent nearly six minutes running full reasoning/memory cycles for 10 repetitions. This card turns that pattern into a cheap execution mode.

Graph-solution classification: this is an ARC runtime control-loop card with graph-memory implications. Use the SideQuest graph as provenance after macro exit, not as a per-step dependency. The useful graph fact is a compact `MacroEpisode` connected to actions, observations, object-progress evidence, and outcome.

## Implementation approach

1. Add a detector for macro eligibility:
   - exactly one available action
   - at least 2 recent executions of that action
   - each execution caused nonzero frame delta
   - no terminal state, env reward, or available-action transition yet
   - no recent repeated-frame/no-op evidence
2. Add a macro execution path in the runner/orchestrator boundary:
   - enter macro mode after eligibility is met
   - call ARC API directly for the repeated action
   - bypass LLM route/execute calls during macro replay
   - defer or suppress blocking MCP calls during macro replay
3. Add stop conditions:
   - `state in WIN/GAME_OVER`
   - `done=True`
   - `available_actions` changes
   - `env_reward != previous env_reward`
   - frame delta is zero
   - frame hash repeats inside the macro window
   - configured cap, default 25 macro actions
4. Add trace/progress metadata:
   - `decision_source=macro_executor`
   - `macro_id`
   - `macro_reason=single_action_deterministic_progress`
   - `macro_step_index`
   - `macro_stop_reason`
5. Add a minimal scoring bridge so dense novelty rewards alone do not count as proof of terminal progress; macro eligibility should use frame-delta consistency, not claim success.
6. Emit one deferred memory/provenance summary after macro exit:
   - stable macro id
   - repeated action id
   - sample count
   - object-progress summary
   - coordinate-relevance status if known
   - stop reason
   - terminal/env outcome
   - no full grid payload

## Concrete file additions/edits

- `agents/arc3/orchestrator.py`
  - Add macro eligibility helper and macro decision metadata.
- `agents/arc3/runner.py`
  - Add bounded macro replay loop around the ARC API step path.
  - Preserve progress log/timeline events for each macro action.
- `agents/arc3/solver.py`
  - Expose any needed lightweight frame-delta/macro state fields without adding MCP calls.
- `tests/test_a061_single_action_macro_executor.py`
  - New regression tests for macro entry, stop conditions, and LLM/MCP bypass.
- Existing tests:
  - `tests/test_arc3_orchestrator.py`
  - `tests/test_arc3_durable_runner.py`

## API/interface changes

- No external API changes.
- Add optional internal config:
  - `macro_executor.enabled: bool = true`
  - `macro_executor.min_confirming_steps: int = 2`
  - `macro_executor.max_macro_steps: int = 25`
- Add optional trace fields on executed steps:
  - `macro_id`
  - `macro_reason`
  - `macro_step_index`
  - `macro_stop_reason`
- Add optional deferred memory summary shape:
  - `macro_episode_id`
  - `action_id`
  - `repeat_count`
  - `progress_evidence`
  - `stop_reason`
  - `outcome`

## Graph-memory model notes

Recommended model: labeled property graph through the existing MCP seam.

Starter provenance shape:

```text
(:PuzzleRun {task_id, game_id})
  -[:HAS_MACRO]->
(:MacroEpisode {macro_id, action_id, repeat_count, stop_reason})
  -[:SUPPORTED_BY]->
(:Evidence {kind:"object_progress", summary, confidence})
  -[:ENDED_WITH]->
(:Outcome {state, env_reward, levels_completed})
```

Implementation rule: write this summary after macro exit or finalization only. Do not query or write the graph inside the macro loop.

## Tests to add or run

Add tests for:

- macro does not enter before confirming evidence
- macro enters after repeated single-action nonzero frame deltas
- macro bypasses LLM calls after entry
- macro bypasses blocking memory calls after entry
- macro stops on available-action change
- macro stops on terminal state
- macro stops on zero delta or repeated frame hash
- progress log keeps all macro actions

Validation commands:

```bash
pytest -q tests/test_a061_single_action_macro_executor.py tests/test_arc3_durable_runner.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Macro mode is execution, not proof of solving. It should not mark success unless the environment does.
- Macro replay must remain bounded to avoid burning the entire benchmark budget on a bad repeated action.
- Memory writes during macro mode may be summarized after macro exit instead of emitted per action.
