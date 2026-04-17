# Plan for B165 — Persist Lessons Across Runs

## Card Metadata
- **Card**: B165
- **Priority**: P2
- **Dependencies**: None

## Summary

Every run starts with 0 memories, 0 lessons, 0 analogies. After a failed run, no knowledge is retained. Add post-run lesson extraction and puzzle fingerprinting so subsequent runs start smarter.

## Technical Approach

### Step 1: Post-run lesson extraction

In `agents/arc3/orchestrator.py`, add `_extract_run_lessons() -> dict`:

```python
def _extract_run_lessons(self) -> dict:
    """Extract lessons from this run for future recall."""
    action_effects = {}
    for action_id, effect in self.observed_action_effects.items():
        action_effects[action_id] = {
            "pixels_changed": effect.get("avg_pixels_changed", 0),
            "reward": effect.get("avg_reward", 0),
            "times_seen": effect.get("times_seen", 0),
            "label": effect.get("value_status", "unknown"),
        }

    return {
        "puzzle_id": self._task_id,
        "game_id": self._game_id,
        "outcome": "solved" if self._solved else "failed",
        "steps_used": len(self._step_history),
        "archetype": str(self._solve_context.get("archetype", "unknown")),
        "victory_condition": str(self._solve_context.get("victory", "unknown")),
        "action_effects": action_effects,
        "zero_effect_actions": [a for a, e in action_effects.items() if e["pixels_changed"] == 0],
        "effective_actions": [a for a, e in action_effects.items() if e["pixels_changed"] > 0],
        "strategy_attempted": self._solve_context.get("strategy_summary", ""),
    }
```

### Step 2: Store lesson via brain

In `agents/arc3/runner.py`, after the main loop completes:

```python
lessons = orchestrator._extract_run_lessons()
await brain.store_lesson(
    content=json.dumps(lessons),
    tags=["arc_run", lessons["archetype"], lessons["outcome"]],
    session_id=session_id,
)
```

### Step 3: Puzzle fingerprint for analogical search

At bootstrap, compute and store a structural fingerprint:

```python
fingerprint = {
    "grid_size": f"{rows}x{cols}",
    "n_colors": len(distinct_colors),
    "n_regions": n_regions,
    "region_size_distribution": sorted_region_sizes,  # e.g. [3, 9, 9, 3006]
    "has_hud": bool(hud_rows),
}
fingerprint_text = f"ARC puzzle {grid_size} {n_colors} colors {n_regions} regions"
```

After run completes, store as analogy anchor:
```python
await brain.notify_turn(
    role="assistant",
    content=f"[PUZZLE ANALOGY] {fingerprint_text}. Outcome: {outcome}. Strategy: {strategy}. Effective actions: {effective}.",
    session_id=session_id,
)
```

### Step 4: Recall at bootstrap

The existing bootstrap flow already calls `recall_relevant_lessons()` and `analogical_search()`. The key change is that these will now return results on subsequent runs because we've stored data.

No code change needed for recall — just ensure the query terms match what we stored. The existing query `"ARC 64x64 grid 5 colors 5 actions"` should match the fingerprint text.

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/orchestrator.py` | Add `_extract_run_lessons()` method |
| `agents/arc3/runner.py` | Call lesson extraction and store after run completes |
| `tests/test_b165_cross_run_lessons.py` | New: test lesson extraction, test storage calls, test recall on subsequent run |

## Acceptance Criteria

1. After a failed run, `brain.store_lesson()` is called with action effect data
2. After a failed run, a puzzle fingerprint analogy is stored via `notify_turn()`
3. `_extract_run_lessons()` correctly identifies zero-effect vs effective actions
4. On a mock subsequent run, `recall_relevant_lessons()` returns the stored lesson
5. `pytest tests/test_b165_cross_run_lessons.py tests/test_arc3_durable_runner.py -q` all pass

## Validation Commands

```bash
pytest tests/test_b165_cross_run_lessons.py -v
pytest tests/test_arc3_durable_runner.py -q
```

## Risks / Constraints

- Lesson storage adds ~200ms to the post-run teardown. Acceptable.
- If the brain's KuzuDB is ephemeral (deleted between runs), lessons won't persist. This is a deployment concern, not a code concern — ensure the DB path is stable.
- Analogy recall quality depends on embedding similarity. The fingerprint text should be structured enough for good matches.
