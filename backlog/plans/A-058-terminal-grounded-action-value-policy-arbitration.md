# Plan: A-058 — terminal-grounded action value and policy arbitration

## Card metadata

- **Card:** A058
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A050, A054

## Summary

Patch the post-A050 live-smoke failure mode where the solver treats dense pixel-change reward as progress while completing zero levels. Add a terminal-grounded action value model and consolidate final action arbitration so autopilot/guard/policy/replan rewrites cannot silently fight each other.

Graph-solution classification: graph is a good fit for the memory portion because the useful decision path is multi-hop and relationship-heavy. Use a labeled property graph mental model over SideQuest memory, with stable entry keys and bounded traversals. Keep ARC runtime behind MCP; do not import graph internals.

## Implementation approach

1. Add a small structured score object for terminal-grounded action value. Keep it local to `agents/arc3/solver.py` unless an existing local dataclass is a better fit.
2. Compute terminal-grounded components from already-available runtime data:
   - `state_after == WIN`
   - `levels_completed` delta
   - player-goal distance delta when both roles and positions are known
   - scene-graph / pattern progress delta from A050 evidence
   - no-op, loop, and repeated zero-terminal-progress penalties
   - dense pixel novelty only as a tie-breaker
3. Replace or wrap action-family ranking paths that currently promote `valuable` based mostly on `reward`, `n_cells_changed`, or novelty.
4. Update plateau selection/expiration so plateau locks require terminal-grounded progress and expire despite dense pixel churn.
5. Consolidate final action arbitration in `agents/arc3/orchestrator.py`:
   - preserve original candidate action
   - record each rewrite proposal internally
   - emit one final executed action with a structured terminal-grounded rationale
   - ensure `override_reason` is never null for `decision_source=policy_override`, `autopilot`, `guard_override`, or `replan_forced_probe`
6. Bound replan-forced probes by run/window counters and require a hypothesis-backed reason.
7. Update failure classification for broad-coverage, zero-level, no-terminal-progress runs.
8. Add a graph-memory prior step that is advisory, bounded, and traceable:
   - entry keys: `scene_wl_hash`, `scene_node_count`, archetype, victory condition, action id, terminal outcome
   - bounded path shape: current `SceneState` -> similar prior `SceneState` -> `ActionEffect` -> `Outcome`
   - output: candidate action priors with evidence path summaries and confidence
   - fallback: neutral prior when MCP lacks graph traversal support or times out
9. Require terminal-grounded local evidence to confirm memory priors before they can sustain plateau locks or overrides.

## Recommended graph memory model

Use SideQuest as a labeled property graph from ARC's perspective.

Core node labels:

- `PuzzleRun`
- `SceneState`
- `Action`
- `ActionEffect`
- `Outcome`
- `PlanChunk`

Core relationship types:

- `OBSERVED`
- `TOOK`
- `PRODUCED`
- `LED_TO`
- `HAD_OUTCOME`
- `SIMILAR_TO`
- `RECOMMENDED`
- `SUPPORTED_BY`

Important properties:

- `scene_wl_hash`
- `frame_hash`
- `archetype`
- `victory_condition`
- `action_id`
- `terminal_value_score`
- `levels_completed_delta`
- `player_goal_delta`
- `scene_progress_delta`
- `confidence`
- `recency`

Hot traversal, expressed as a provider-neutral pattern:

```text
SceneState(scene_wl_hash or bounded-similar hash)
  -> SIMILAR_TO*0..1
  -> prior SceneState
  -> TOOK Action
  -> PRODUCED ActionEffect
  -> HAD_OUTCOME Outcome
filter Outcome.levels_completed_delta > 0 or terminal_value_score > threshold
return action_id, confidence, path_summary
limit N
```

Practical graph rules:

- Index entry points, not traversals: `scene_wl_hash`, `frame_hash`, `action_id`, `archetype + victory_condition`.
- Bound variable paths to 0-1 similarity hops and 2-4 total hops in the hot path.
- Filter early by current archetype/victory condition before expanding.
- Treat generic hubs such as `race`, `reach_goal`, and `ACTION7` as dense-node risks; never traverse from those alone.
- Persist evidence path summaries into trace so a memory prior is explainable.

## Concrete file additions/edits

- `agents/arc3/solver.py`
  - Add terminal-grounded action scoring.
  - Demote dense novelty in action-family evidence.
  - Update plateau lock scoring and expiration.
- `agents/arc3/orchestrator.py`
  - Consolidate action rewrite flow.
  - Ensure all rewrite paths emit before/after action ids and structured reasons.
  - Add bounded replan probe checks if those live here.
  - Thread advisory graph-memory priors into arbitration without letting them override terminal-grounded negative evidence.
- `agents/arc3/runner.py`
  - Thread terminal-grounded score components into `progress_log`, `arc_event_timeline`, and master timeline payloads.
  - Add graph-memory evidence path summaries to trace payloads when present.
- `agents/arc3/failure_taxonomy.py`
  - Add or reuse a more precise zero-terminal-progress classification.
- `tests/test_a058_terminal_grounded_policy.py`
  - New focused regression tests for dense-reward demotion, plateau expiration, rewrite attribution, and probe budgets.
- Existing tests to update as needed:
  - `tests/test_arc3_solver.py`
  - `tests/test_arc3_orchestrator.py`
  - `tests/test_arc3_durable_runner.py`

## API/interface changes

- No external API changes.
- Internal trace schema gains optional terminal-grounded score fields, for example:
  - `terminal_value_score`
  - `terminal_value_components`
  - `terminal_progress_reason`
  - `rewrite_chain`
  - `final_action_owner`
  - `memory_prior_action`
  - `memory_prior_confidence`
  - `memory_prior_path_summary`
  - `memory_prior_source: graph|text|cache|none`
- Preserve existing trace fields for compatibility.
- If existing MCP tools cannot return path summaries, use compact deterministic summaries from the available response and add a sibling `sidequests-brain` follow-up rather than bypassing MCP.

## Tests to add or run

Add tests that reproduce the live-smoke shape:

- A sequence with positive dense rewards and many changed pixels but no `levels_completed` delta should not mark the action family as terminal-valuable.
- A plateau lock with repeated dense-only progress expires.
- A policy/autopilot/guard rewrite produces non-null structured `override_reason` and before/after action fields.
- Replan-forced probes are capped and include a terminal-grounded reason.
- A zero-level, broad-coverage terminal-stall classifies as a terminal/strategy failure rather than generic loop when frame hashes are not actually looping.
- A memory prior based on a similar scene graph can promote an action candidate only as an advisory score; local terminal-grounded negative evidence rejects it.
- A generic archetype-only memory hit cannot trigger policy override or plateau lock.

Validation commands:

```bash
pytest -q tests/test_a058_terminal_grounded_policy.py tests/test_arc3_solver.py tests/test_arc3_orchestrator.py tests/test_arc3_durable_runner.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Dense novelty remains useful for exploration tie-breaking, but its default weight should be low and capped.
- If role positions are unknown, the value model should fall back to scene-graph progress and terminal state, not to dense reward dominance.
- The implementation should avoid new per-step MCP calls unless A058 demonstrates that an existing gated retrieval point cannot supply graph-memory priors. Any added call must be bounded, cached, and visible in trace.
- Any memory-derived action prior must pass through the existing MCP client seam and must not override terminal-grounded negative evidence.
