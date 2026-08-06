# Plan: A-131 — Planner Action Diversity

## Context

The resolve → plan pipeline always picks `probe-grid-2x2`. The planner has no exploration pressure — it greedily re-selects whatever worked (or didn't fail) last time.

## Approach

### 1. Add exploration penalty to candidate scoring

In the planning phase, apply a decay factor based on `state.action_attempt_counts[action_id]`. Something like:

```
adjusted_score = raw_score * (decay_factor ** attempt_count)
```

Where `decay_factor` ≈ 0.7, so after 3 attempts the score is halved.

### 2. Force exploration after stagnation

If `state.consecutive_no_progress_count >= 2` and the same action was used for the last N steps, inject untested actions from `arc_get_untested_actions` as candidates with a baseline score boost.

### 3. Wire available_actions into planning context

The observation includes `available_actions`. Pass this into the plan phase so the planner knows the full action space, not just what the graph suggests.

### 4. Add diversity metric to telemetry

Emit `unique_actions_tried` and `action_entropy` in step snapshots for monitoring.

## Files to modify

- `agents/arc4/planner.py` — scoring adjustment, exploration injection
- `agents/arc4/graph_queries.py` — may need `get_untested_actions` call
- `agents/arc4/telemetry.py` — diversity metrics

## Risks

- Over-exploration wastes budget on bad actions. The decay factor needs tuning.
- The graph world model may not have enough data to suggest good alternatives early in a run.
