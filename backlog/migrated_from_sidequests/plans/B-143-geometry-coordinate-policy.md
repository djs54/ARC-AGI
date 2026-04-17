# Plan for B143 — Geometry-Aware Coordinate Policy for space/reach_goal Puzzles

## Card Metadata

- **Card ID**: B143
- **Priority**: P1
- **Dependencies**: B141 (bail-out ensures bad coordinates get abandoned), B142 (graduation fix enables replanning)

## Summary

`_candidate_action6_coordinates()` builds a naive candidate list and `_infer_action6_coordinates()` cycles through it via modulo. For `space/reach_goal` puzzles with known player/goal positions, the policy should bias toward goal-directed coordinates instead of random cycling. This plan replaces the coordinate policy with a geometry-aware version when conditions are met.

## Verified Baseline

From `live_gemma4_e4b_timeout_1775221087`:
- Coordinates visited: (0,0), (33,2), (2,0), (0,3), (0,4), (0,5), (35,0), (36,1), (37,2), (37,1), (38,1), (38,2), (38,1)
- Player position: ~(33-38, 0-2) with confidence 0.90
- Goal position: role 9 with confidence 0.83
- No coordinates tested between player and goal regions
- Several coordinates clustered in a 3×3 box around the player with zero reward

## Technical Approach

### 1. Goal-directed candidate sorting

In `_candidate_action6_coordinates()`, add a conditional branch when:
- `archetype == "space"`
- `victory_condition == "reach_goal"`
- Both player and goal positions are known from `_solve_context`

When conditions are met, build candidates in priority tiers:

```python
def _candidate_action6_coordinates_goaldir(self, player_pos, goal_pos, grid):
    candidates = []
    
    # Tier 1: Coordinates on player→goal vector (within ±2 cells)
    # Use Bresenham-like line between player and goal, expand by ±2
    for coord in self._coords_along_vector(player_pos, goal_pos, margin=2):
        candidates.append(("goal_vector", coord))
    
    # Tier 2: Reduce Manhattan distance from player to goal
    # Sort remaining non-background pixels by distance reduction potential
    for coord in self._non_background_pixels(grid):
        if coord not in [c for _, c in candidates]:
            dist_to_goal = manhattan(coord, goal_pos)
            candidates.append(("distance_reduce", coord))
    candidates_tier2 = sorted(candidates_tier2, key=lambda c: manhattan(c, goal_pos))
    
    # Tier 3: Original heuristic (center, corners)
    candidates.extend([("fallback", c) for c in self._original_candidates(grid)])
    
    return candidates
```

### 2. Directional momentum bias

Track the last 2+ player position deltas from frame analysis. If a consistent direction is detected (e.g., player moved right twice), bias the next coordinate to continue that direction:

```python
def _apply_momentum_bias(self, candidates, recent_deltas):
    if len(recent_deltas) < 2:
        return candidates
    avg_dx = sum(d[0] for d in recent_deltas) / len(recent_deltas)
    avg_dy = sum(d[1] for d in recent_deltas) / len(recent_deltas)
    if abs(avg_dx) < 0.5 and abs(avg_dy) < 0.5:
        return candidates  # no consistent direction
    # Re-sort by alignment with momentum direction
    ...
```

### 3. Anti-clustering guard

Track recent coordinate attempts. If 3+ consecutive coordinates fall within a 3×3 bounding box and all produced zero reward, skip that region:

```python
def _is_cluster_exhausted(self, coord, recent_attempts, min_count=3):
    nearby = [a for a in recent_attempts[-min_count:]
              if abs(a.x - coord[0]) <= 1 and abs(a.y - coord[1]) <= 1]
    return (len(nearby) >= min_count and 
            all(a.reward == 0.0 for a in nearby))
```

### 4. Fallback for non-space archetypes

The existing `_candidate_action6_coordinates()` and `_infer_action6_coordinates()` remain as-is for any archetype that is NOT `space/reach_goal`. The new logic is an additive conditional branch, not a replacement.

## Concrete File Changes

### `agents/arc3/orchestrator.py`
- `_candidate_action6_coordinates()` (~L1633-1681): Add conditional branch for space/reach_goal with goal-directed sorting
- `_infer_action6_coordinates()`: Replace modulo cycling with tier-aware selection when goal-directed candidates are available; add anti-clustering skip
- Add helper: `_coords_along_vector(start, end, margin)` — generates coordinates along a line
- Add helper: `_apply_momentum_bias(candidates, recent_deltas)` — re-sorts by directional alignment
- Add helper: `_is_cluster_exhausted(coord, recent_attempts)` — detects exhausted 3×3 regions
- Add trace fields: `coordinate_policy` ("goal_directed" | "default"), `goal_distance_before`, `goal_distance_after`, `directional_bias`, `cluster_skipped`

### `tests/test_b143_coordinate_policy.py` (new)
- Test goal-directed sorting: for known player+goal, verify first 3 candidates lie on/near the vector
- Test momentum bias: consistent player movement → next candidate continues direction
- Test anti-clustering: 3+ zero-reward attempts in 3×3 box → region skipped
- Test fallback: non-space archetype → original coordinate policy unchanged
- Test trace fields: verify `coordinate_policy` and distance fields in output

## API/Schema/Test Updates

- No tool catalog changes
- No adapter allow-list changes
- No schema changes
- Trace output gains coordinate policy fields (additive, non-breaking)

## Acceptance Criteria

- [ ] For `space/reach_goal` with known player+goal, first 3 candidates lie on/near player→goal vector
- [ ] Directional momentum detected from frame deltas biases next coordinate choice
- [ ] Anti-clustering skips regions after 3+ zero-reward attempts in 3×3 box
- [ ] Non-space archetypes use existing coordinate policy unchanged
- [ ] Trace output includes `coordinate_policy`, `goal_distance_before/after`
- [ ] Existing test suites pass with no regressions

## Validation Commands

```bash
.venv/bin/python -m pytest tests/test_b143_coordinate_policy.py -q
.venv/bin/python -m pytest tests/ -q --timeout=60
```

## Risks / Constraints

- Player and goal positions must be extracted from `_solve_context` role data — verify the exact field paths
- The vector coordinate generation must clamp to valid grid bounds (0-63)
- Momentum bias requires frame-over-frame player position tracking — verify if this data is already captured or needs to be added
- This card is most effective after B141 (bail-out) and B142 (graduation fix) are in place — without them, the agent will still exhaust all coordinates even if they're better ordered

## Done When

- Goal-directed candidates appear first in unit tests
- Anti-clustering skips exhausted regions
- Non-space puzzles are unaffected
- A live smoke shows coordinates biased toward the goal
