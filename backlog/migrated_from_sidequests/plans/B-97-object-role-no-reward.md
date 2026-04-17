# B-97 — Object Role Detection Without Reward Signal

## Metadata
- Card: B97
- Priority: P0
- Dependencies: B95

## Summary

Replace the reward-gated `ObjectRoleMapper` with grid-change-pattern detection.
ARC-AGI-3 provides no per-step reward, so the current heuristics never fire.
The new implementation uses:
1. **Operator-effect correlation** — which color centroid or changed-region signature moves with the currently inferred operator effect
2. **Static row exclusion** — colors only in static/HUD rows → WALL
3. **Stationary small-object heuristic** — inert, small, non-background → GOAL
4. **Evidence fusion** — no single rule is authoritative; roles accumulate from multiple weak signals

`SolveEngine` owns any SideQuests retrieval. `ObjectRoleMapper` should remain mostly logic-only and
consume already-fetched inputs.

## Technical Approach

### Grid centroid computation

Given a `grid` (64×64 list-of-lists of int 0–15), compute per-color centroids:

```python
def _compute_centroids(grid: List[List[int]]) -> Dict[int, Dict[str, float]]:
    """Returns {color_id: {"row": mean_row, "col": mean_col, "count": pixel_count}}"""
    from collections import defaultdict
    acc: Dict[int, list] = defaultdict(list)
    for r, row in enumerate(grid):
        for c, val in enumerate(row):
            acc[val].append((r, c))
    result = {}
    for color, pixels in acc.items():
        rows = [p[0] for p in pixels]
        cols = [p[1] for p in pixels]
        result[color] = {
            "row": sum(rows) / len(rows),
            "col": sum(cols) / len(cols),
            "count": len(pixels),
        }
    return result
```

### Operator-effect evidence

```python
# Do not hard-code ACTION1..ACTION4 to compass directions.
# Instead consume operator semantics already inferred by the explore phase:
# - trend.direction from saved action facts
# - changed_region motion across repeated transitions
# - path hypotheses showing which operators reliably move the active region
```

### ObjectRoleMapper rewrite

Replace the entire class body. Keep the same `update()` signature:
`update(self, hypothesis_context, observation, step) -> Dict[int, ObjectRole]`

```python
class ObjectRoleMapper:
    """Assigns semantic roles to color groups from grid change patterns.

    No reward signal required. Uses:
      - Operator-effect evidence → PLAYER
      - static_rows coverage → WALL
      - Small + stationary + non-background + non-static → GOAL
    """

    MIN_PLAYER_OBSERVATIONS: int = 2      # need N consistent directional matches
    PLAYER_CONFIDENCE: float = 0.75
    WALL_CONFIDENCE: float = 0.70
    GOAL_CONFIDENCE: float = 0.60
    GOAL_MAX_COUNT_FRACTION: float = 0.02  # goal is ≤ 2% of total pixels (64*64=4096 → ≤82px)
    BACKGROUND_COLOR: int = 0

    def __init__(self) -> None:
        self._prev_centroids: Dict[int, Dict[str, float]] = {}
        # For each color: accumulated movement/interaction evidence from observed operator effects
        self._movement_evidence: Dict[int, List[Dict]] = {}
        self._stationary_steps: Dict[int, int] = {}   # color → count of steps it didn't move

    def update(
        self,
        hypothesis_context: Dict[str, Any],
        observation: Dict[str, Any],
        step: int,
    ) -> Dict[int, ObjectRole]:
        grid = observation.get("grid") or []
        colors_info = observation.get("colors") or []
        static_rows = set(hypothesis_context.get("static_rows") or [])
        hud_rows = set(hypothesis_context.get("hud_rows") or [])
        action_taken = (hypothesis_context.get("last_transition_effect") or {}).get("action")
        action_facts = hypothesis_context.get("action_facts") or []

        # Compute current centroids
        curr_centroids = _compute_centroids(grid) if grid else {}
        total_pixels = sum(v["count"] for v in curr_centroids.values()) or 1

        roles: Dict[int, ObjectRole] = {}
        for color_info in colors_info:
            color_id = color_info["value"] if isinstance(color_info, dict) else color_info
            role = ObjectRole(color_id=color_id, evidence_steps=[step])
            roles[color_id] = role

        # ── WALL detection ─────────────────────────────────────────────
        if static_rows and grid:
            grid_height = len(grid)
            for color_id, centroid in curr_centroids.items():
                if color_id not in roles:
                    continue
                # Check if ALL pixels of this color are in static or HUD rows
                all_static = True
                for r, row in enumerate(grid):
                    for c, val in enumerate(row):
                        if val == color_id and r not in static_rows and r not in hud_rows:
                            all_static = False
                            break
                    if not all_static:
                        break
                if all_static and color_id != self.BACKGROUND_COLOR:
                    roles[color_id].role = RoleType.WALL
                    roles[color_id].confidence = self.WALL_CONFIDENCE

        # ── PLAYER detection via operator-effect correlation ─────────────
        inferred_operator_direction = _inferred_direction_for_action(action_taken, action_facts)
        if inferred_operator_direction and self._prev_centroids:
            exp_dr, exp_dc = inferred_operator_direction
            for color_id, curr in curr_centroids.items():
                prev = self._prev_centroids.get(color_id)
                if prev is None:
                    continue
                actual_dr = curr["row"] - prev["row"]
                actual_dc = curr["col"] - prev["col"]
                # Sign match: did centroid move in the expected direction?
                dr_match = (exp_dr == 0) or (exp_dr * actual_dr > 0)
                dc_match = (exp_dc == 0) or (exp_dc * actual_dc > 0)
                moved = abs(actual_dr) > 0.5 or abs(actual_dc) > 0.5
                if color_id not in self._movement_evidence:
                    self._movement_evidence[color_id] = []
                self._movement_evidence[color_id].append({
                    "action": action_taken,
                    "match": dr_match and dc_match and moved,
                    "step": step,
                })

        # Evaluate player candidates: color with strongest consistent movement evidence
        best_player: Optional[int] = None
        best_match_rate: float = 0.0
        for color_id, evidence in self._movement_evidence.items():
            if len(evidence) < self.MIN_PLAYER_OBSERVATIONS:
                continue
            matches = sum(1 for e in evidence if e["match"])
            rate = matches / len(evidence)
            if rate > best_match_rate and rate >= 0.6:
                best_match_rate = rate
                best_player = color_id

        if best_player is not None and best_player in roles:
            curr = curr_centroids.get(best_player, {})
            roles[best_player].role = RoleType.PLAYER
            roles[best_player].confidence = min(self.PLAYER_CONFIDENCE + best_match_rate * 0.1, 0.90)
            if curr:
                roles[best_player].estimated_position = {"row": curr["row"], "col": curr["col"]}

        # ── GOAL detection ─────────────────────────────────────────────
        # Track stationary steps (didn't move between frames)
        for color_id, curr in curr_centroids.items():
            prev = self._prev_centroids.get(color_id)
            if prev is not None:
                moved = abs(curr["row"] - prev["row"]) > 0.5 or abs(curr["col"] - prev["col"]) > 0.5
                if not moved:
                    self._stationary_steps[color_id] = self._stationary_steps.get(color_id, 0) + 1
                else:
                    self._stationary_steps[color_id] = 0

        for color_id, centroid in curr_centroids.items():
            if color_id not in roles:
                continue
            if roles[color_id].role != RoleType.UNKNOWN:
                continue  # already classified
            if color_id == self.BACKGROUND_COLOR:
                continue
            count_fraction = centroid["count"] / total_pixels
            stationary = self._stationary_steps.get(color_id, 0)
            in_hud = all(
                r in hud_rows
                for r, row in enumerate(grid)
                for c, val in enumerate(row)
                if val == color_id
            ) if hud_rows and grid else False
            in_static_rows = all(
                r in static_rows
                for r, row in enumerate(grid)
                for c, val in enumerate(row)
                if val == color_id
            ) if static_rows and grid else False
            if (
                count_fraction <= self.GOAL_MAX_COUNT_FRACTION
                and stationary >= 2
                and not in_hud
                and not in_static_rows
            ):
                roles[color_id].role = RoleType.GOAL
                roles[color_id].confidence = self.GOAL_CONFIDENCE
                roles[color_id].estimated_position = {"row": centroid["row"], "col": centroid["col"]}

        # Save centroids for next step
        self._prev_centroids = curr_centroids
        return roles
```

Also add the module-level helper function at the top of the `ObjectRoleMapper` section (before the class):

```python
def _compute_centroids(grid: List[List[int]]) -> Dict[int, Dict[str, float]]:
    """Return {color_id: {row, col, count}} centroids from a 64x64 grid."""
    from collections import defaultdict
    acc: Dict[int, list] = defaultdict(list)
    for r, row in enumerate(grid):
        for c, val in enumerate(row):
            acc[val].append((r, c))
    return {
        color: {
            "row": sum(p[0] for p in pixels) / len(pixels),
            "col": sum(p[1] for p in pixels) / len(pixels),
            "count": len(pixels),
        }
        for color, pixels in acc.items()
    }

def _inferred_direction_for_action(action_id: str | None, action_facts: List[Dict[str, Any]]) -> tuple[int, int] | None:
    """Translate already-inferred action-fact trend into an expected centroid delta.

    This helper consumes evidence from the explore phase. It must not hard-code ACTION ids to
    compass directions.
    """
    if not action_id:
        return None
    fact = next((item for item in action_facts if item.get("action") == action_id), None)
    trend = (fact or {}).get("trend") or {}
    direction = trend.get("direction")
    if direction == "up":
        return (-1, 0)
    if direction == "down":
        return (1, 0)
    if direction == "left":
        return (0, -1)
    if direction == "right":
        return (0, 1)
    return None
```

## Files to Modify

- `agents/arc3/solver.py`:
  - Add `_compute_centroids()` module-level helper before `ObjectRoleMapper`
  - Add `ACTION_DIRECTION` dict before `ObjectRoleMapper`
  - Replace `ObjectRoleMapper` class body entirely (keep same class name and `update()` signature)

## Tests to Add

Add to `tests/test_arc3_solver.py`:

```python
def _make_grid_with_color_at(color: int, rows: List[int], cols: List[int], size: int = 10) -> List[List[int]]:
    """Helper: make a size×size grid with given color at specified (row, col) pairs."""
    grid = [[0] * size for _ in range(size)]
    for r, c in zip(rows, cols):
        grid[r][c] = color
    return grid


@pytest.mark.asyncio
async def test_object_role_mapper_detects_player_from_movement():
    """Color that moves with the inferred operator direction for 2 steps is assigned PLAYER."""
    mapper = ObjectRoleMapper()

    # Step 0: color 3 at row=5, col=3
    grid0 = _make_grid_with_color_at(3, [5], [3])
    obs0 = {"grid": grid0, "colors": [{"value": 3, "count": 1}]}
    hyp0 = {"last_transition_effect": {"action": None}, "static_rows": [], "hud_rows": []}
    mapper.update(hyp0, obs0, 0)

    # Step 1: inferred operator effect points upward — color 3 moves to row=4
    grid1 = _make_grid_with_color_at(3, [4], [3])
    obs1 = {"grid": grid1, "colors": [{"value": 3, "count": 1}]}
    hyp1 = {
        "last_transition_effect": {"action": "ACTION1"},
        "action_facts": [{"action": "ACTION1", "trend": {"direction": "up"}}],
        "static_rows": [],
        "hud_rows": [],
    }
    mapper.update(hyp1, obs1, 1)

    # Step 2: same inferred operator effect again — color 3 moves to row=3
    grid2 = _make_grid_with_color_at(3, [3], [3])
    obs2 = {"grid": grid2, "colors": [{"value": 3, "count": 1}]}
    hyp2 = {
        "last_transition_effect": {"action": "ACTION1"},
        "action_facts": [{"action": "ACTION1", "trend": {"direction": "up"}}],
        "static_rows": [],
        "hud_rows": [],
    }
    roles = mapper.update(hyp2, obs2, 2)

    assert roles[3].role == RoleType.PLAYER
    assert roles[3].confidence >= 0.70


@pytest.mark.asyncio
async def test_object_role_mapper_detects_wall_from_static_rows():
    """Color exclusively in static rows is assigned WALL."""
    mapper = ObjectRoleMapper()
    # Color 2 only appears in row 0 (a static row)
    grid = [[2, 2, 2, 0, 0], [0, 0, 0, 0, 0], [0, 0, 0, 0, 0]]
    obs = {"grid": grid, "colors": [{"value": 2, "count": 3}]}
    hyp = {"last_transition_effect": {}, "static_rows": [0], "hud_rows": []}
    roles = mapper.update(hyp, obs, 0)
    assert roles[2].role == RoleType.WALL
    assert roles[2].confidence >= 0.65


@pytest.mark.asyncio
async def test_object_role_mapper_detects_goal_heuristic():
    """Small, stationary, non-background color becomes GOAL after 2 static steps."""
    mapper = ObjectRoleMapper()
    size = 20
    # Color 7 at fixed position (5,5), very small (1 pixel out of 400)
    def make_obs():
        grid = [[0] * size for _ in range(size)]
        grid[5][5] = 7
        return {"grid": grid, "colors": [{"value": 0, "count": 399}, {"value": 7, "count": 1}]}

    hyp = {"last_transition_effect": {}, "static_rows": [], "hud_rows": []}
    mapper.update(hyp, make_obs(), 0)
    mapper.update(hyp, make_obs(), 1)
    roles = mapper.update(hyp, make_obs(), 2)

    assert roles[7].role == RoleType.GOAL
    assert roles[7].confidence >= 0.55
```

## Validation Commands

```bash
pytest -q tests/test_arc3_solver.py tests/test_arc3_orchestrator.py tests/test_arc3_hypothesis.py
```

All 97 tests (94 existing + 3 new) must pass.

Then run live:
```bash
export ARC_API_KEY="$(python3 -c 'import json; print(json.load(open("benchmarks/.arc/arc.json"))["key"])')"
.venv/bin/python run_single_puzzle.py --real-api --num-puzzles 1 --card-id b97_role_detection
```

Verify in `[SOLVE]` logs that object_roles no longer shows all-unknown by step 5–8.

## Risks

- Grid iteration is O(64×64) per step — fast enough, ~4096 iterations.
- `in_hud` check has an inner loop that could be slow on large grids; simplified by checking row index only (acceptable approximation).
- False GOAL assignments possible if background color varies; mitigated by `BACKGROUND_COLOR = 0` exclusion and low count fraction threshold.
