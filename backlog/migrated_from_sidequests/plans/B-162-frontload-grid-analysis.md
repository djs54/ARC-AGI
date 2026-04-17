# Plan for B162 — Front-Load Grid Analysis Before First Action

## Card Metadata
- **Card**: B162
- **Priority**: P1
- **Dependencies**: None

## Summary

Run `grid_characteristic_summary()` at bootstrap so the agent's first action is informed by the grid's spatial structure, not blind exploration.

## Technical Approach

### Step 1: Call grid analysis at bootstrap

In `agents/arc3/orchestrator.py`, in the bootstrap phase (after `perceive` but before `act`):

```python
from agents.arc3.grid_analysis import grid_characteristic_summary

grid_summary = grid_characteristic_summary(initial_grid)
self._bootstrap_grid_summary = grid_summary
```

Emit trace event:
```python
self._emit_trace_event("operation", "bootstrap_grid_analysis", {
    "n_regions": grid_summary.get("n_regions", 0),
    "colors": grid_summary.get("distinct_colors", []),
    "summary": grid_summary.get("text_summary", "")[:200],
})
```

### Step 2: Inject into first prompt

In `build_action_packet()`, when `step == 0` (or step history is empty):
- Add a `GRID_ANALYSIS` content block:
  ```
  === GRID ANALYSIS ===
  {grid_summary.text_summary}
  ```
- This block is already defined in the `PromptPacket.render()` ordered_keys, so it will be placed correctly.

### Step 3: Pre-populate role hints

Pass the grid summary to the `SolveEngine` before the first solve phase:
- If grid analysis identifies small isolated regions, hint them as candidate player/goal
- If grid analysis identifies large contiguous regions, hint them as walls/background
- Store as soft priors in `ObjectRoleMapper` (confidence 0.3, source "grid_analysis")

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/orchestrator.py` | Call `grid_characteristic_summary()` in bootstrap, inject `GRID_ANALYSIS` block for step 0, pass hints to SolveEngine |
| `tests/test_b162_frontload_analysis.py` | New: test grid analysis appears in step-0 prompt, test trace event emitted |

## Acceptance Criteria

1. `build_action_packet()` for step 0 contains a `GRID_ANALYSIS` block
2. `"bootstrap_grid_analysis"` trace event is emitted with region count and color list
3. `pytest tests/test_b162_frontload_analysis.py tests/test_arc3_orchestrator.py -q` all pass

## Validation Commands

```bash
pytest tests/test_b162_frontload_analysis.py -v
pytest tests/test_arc3_orchestrator.py -q
```

## Risks / Constraints

- `grid_characteristic_summary()` must handle 64x64 grids efficiently (< 50ms). It's already designed for this scale.
- The grid analysis block adds tokens to the prompt. On a 1200-token budget this is tight — but B164 raises the budget to 1800.
