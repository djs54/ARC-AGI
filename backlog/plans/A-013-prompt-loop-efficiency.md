# A-013 - Prompt Route/Execute Loop Wastes Steps With Redundant Rationales

## Card metadata

- Card: A013
- Priority: P1
- Depends on: A010, A011

## Summary

Tighten the ARC prompt assembly so that repeated identical observations do not produce repeated LLM calls, fix the observation-block label bug that calls the background color the "goal", and collapse duplicate history entries so the LLM sees the aggregate signal instead of a scrolling list.

## Implementation approach

1. Locate prompt-block rendering. Based on `prompt_trace[i].block_trace`, blocks include `ObservationBlock`, `ActionFactBlock`, `HistoryBlock`, `InstructionBlock`. Find the renderer module (likely under `agents/arc3/` or `benchmarks/arc3/` — confirm with a Grep for `ObservationBlock`).
2. Fix the goal-labeling bug: in the top-colors renderer, use `solve_ctx.victory_condition.goal_color` (or whatever the grounded goal color key is) to tag `(goal)`, and `solve_ctx.player_role.color` for `(player)`. Do not tag the largest-count color as goal unless it is the grounded goal.
3. Add a per-step observation fingerprint:
   - `fingerprint = (frame_hash, tuple(available_actions), grounded_roles_sig)`
   - store the last-prompted fingerprint in the orchestrator
   - if the current fingerprint matches and no new `action_facts` have been added since the last LLM call, either:
     - short-circuit the LLM call by reusing the last action choice and logging `prompt_skip_noop` with the count of skipped steps, OR
     - emit a one-line "same observation; pick ACTION6 again" directive (configurable)
4. Collapse the history block: group adjacent identical `(action_id, rationale_hash)` entries into `"Step X–Y → ACTION6 × (Y-X+1) · same rationale"`.
5. Aggregate action facts: render all `action_facts` entries for the current action with their observation count and consistency, not just the latest.
6. Add regression test fixtures covering multi-action puzzles so the short-circuit does not accidentally fire when the observation genuinely changed.

## Concrete file additions/edits

- edit the observation-block renderer (confirm path via Grep)
- edit `agents/arc3/orchestrator.py` — fingerprint tracking, prompt-skip-noop path
- edit history-block renderer
- add `tests/test_prompt_block_rendering.py`
- add `tests/test_prompt_skip_noop.py`

## API/interface changes

- new trace event name `prompt_skip_noop` with metadata `{reason, skipped_steps, last_action}`
- block renderers may gain an optional `roles_context` param so they can correctly label colors

## Tests to add or run

- `pytest -q tests/test_prompt_block_rendering.py`
- `pytest -q tests/test_prompt_skip_noop.py`
- re-run the one-puzzle smoke and confirm `prompt_trace` for steady-state steps is shorter or emits `prompt_skip_noop`

## Validation commands

- `pytest -q -k prompt_`
- manual diff of prompt sizes across `submission_results_single.json.prompt_trace` entries before/after

## Assumptions/defaults

- the `frame_hash` field already present in `final_observation` is also available per-step upstream; if not, the implementation must compute it
- configuring "skip LLM entirely" vs "emit collapsed one-liner" defaults to the collapsed one-liner for safety (the LLM stays in the loop but pays ~30 tokens instead of ~1500)
- grounded role colors come from `solve_ctx`; no memory seam changes are required
