# Plan for B167 — Pattern-Aware Puzzle Strategy

## Card Metadata
- **Card**: B167
- **Priority**: P0
- **Dependencies**: B166 (autopilot), B150 (grid diff engine)

## Summary

ARC-AGI-3 puzzles require the agent to match/complete patterns by visiting intermediate objects before the final goal becomes solvable. This card adds region comparison, intermediate goal discovery, and phase-aware autopilot targeting.

## Design Principles

1. **Deterministic over LLM** — region comparison and target selection are computed, not prompted
2. **Build on existing infra** — `GridDiffEngine` already has connected components, bounding boxes, color mapping
3. **Incremental** — each component is independently testable and useful
4. **Graceful fallback** — if region pairing fails, fall back to existing autopilot (drive to goal)

## Technical Approach

### Part 1: Region extraction and comparison (`grid_analysis.py`)

#### 1a. Extract bounded pattern regions

The grid contains several types of regions:
- **Large background** (color 0 or 1, covers most of the grid) — ignore
- **Player** (small, moves between frames) — already tracked
- **Pattern regions** (medium-sized, structured, in specific locations) — reference and goal
- **Small markers** (isolated 1-5 cell objects) — intermediate interaction targets
- **HUD/border** (bottom row, edges) — decoration

```python
@dataclass
class PatternRegion:
    """A bounded rectangular region containing a meaningful pattern."""
    bounding_box: tuple[int, int, int, int]  # (min_row, min_col, max_row, max_col)
    pattern: List[List[int]]  # Cropped sub-grid (colors within bounding box)
    center: tuple[float, float]  # (row, col) centroid
    color_palette: Set[int]  # Unique colors in region (excluding background)
    size: int  # Number of non-background cells
    location_hint: str  # "corner_bl", "corner_br", "center", "edge_top", etc.


def extract_pattern_regions(grid: List[List[int]],
                            background_color: int = 0,
                            min_size: int = 4,
                            max_size_fraction: float = 0.25) -> List[PatternRegion]:
    """Find distinct pattern regions in the grid.

    Strategy:
    1. Use extract_connected_components() to find all regions
    2. Filter out the background (largest region of color 0 or dominant color)
    3. Filter out single-cell regions (noise)
    4. Group nearby same-color cells into bounding boxes
    5. For each bounding box, crop the sub-grid as a PatternRegion
    6. Annotate with location_hint based on position relative to grid center

    Returns regions sorted by size descending.
    """
```

#### 1b. Compare two pattern regions

```python
@dataclass
class RegionComparison:
    similarity: float  # 0.0 to 1.0
    exact_match: bool
    cells_matching: int
    cells_total: int
    color_shift: Optional[Dict[int, int]]  # If patterns match with a color swap
    description: str  # "exact match", "partial match (72%)", "color-shifted match", "no match"


def compare_regions(region_a: PatternRegion, region_b: PatternRegion,
                    allow_color_shift: bool = True) -> RegionComparison:
    """Compare two pattern regions for similarity.

    Strategy:
    1. If sizes differ significantly (>2x), return no match
    2. Normalize both to same dimensions (pad smaller to larger)
    3. Cell-by-cell comparison:
       a. Exact: same color at same position → match
       b. Color-shifted: consistent mapping (e.g., all red→blue) → shifted match
       c. Structural: same non-background/background pattern regardless of color → partial match
    4. Return similarity = cells_matching / cells_total
    """
```

#### 1c. Find reference/goal pair

```python
def find_reference_goal_pair(
    regions: List[PatternRegion],
    grid_rows: int, grid_cols: int
) -> Optional[tuple[PatternRegion, PatternRegion]]:
    """Identify which regions are reference (static target) and goal (dynamic).

    Heuristics:
    1. Look for two regions of similar size (within 2x)
    2. Prefer regions in corners or edges as "reference" (reference patterns
       are often in corners — bottom-left, top-right, etc.)
    3. The reference region is typically more "isolated" (farther from player/action area)
    4. If two similar-sized regions exist and one is in a corner, that's the reference
    5. The other is the goal (the one the player needs to modify)

    Returns (reference, goal) or None if no plausible pair found.
    """
```

### Part 2: Intermediate object discovery (`solver.py`)

#### 2a. Add "intermediate" role to ObjectRoleMapper

Currently roles are: player, goal, wall, decoration, unknown. Add "intermediate" for small interactive objects.

In `ObjectRoleMapper._classify_role()`, add logic:

```python
# Intermediate detection: small isolated objects that aren't player/background
# These are potential interaction targets (crosses, markers, switches)
if (
    region_size >= 2 and region_size <= 20  # Small but not single-cell
    and not is_player_color
    and not is_background_color
    and not is_wall_candidate
    and change_count >= 1  # Has changed at least once (interactive)
):
    return "intermediate", 0.6
```

Also detect "intermediate" from spatial proximity: if a small object is on a navigable path between player and goal, it's likely an intermediate target.

#### 2b. Add PatternMatchTracker to SolveEngine

```python
class PatternMatchTracker:
    """Tracks whether the goal region is converging toward the reference pattern."""

    def __init__(self):
        self.reference_region: Optional[PatternRegion] = None
        self.goal_region: Optional[PatternRegion] = None
        self.similarity_history: List[float] = []  # Per-step similarity scores
        self.phase: str = "discover"  # "discover" → "intermediate" → "finish"

    def update(self, grid: List[List[int]], step: int) -> dict:
        """Called each step. Returns phase and similarity info."""
        # 1. If reference/goal not yet identified, try to find them
        if self.reference_region is None:
            regions = extract_pattern_regions(grid)
            pair = find_reference_goal_pair(regions, len(grid), len(grid[0]))
            if pair:
                self.reference_region, self.goal_region = pair

        # 2. If both known, compare current goal state to reference
        if self.reference_region and self.goal_region:
            # Re-extract goal region from current grid (it may have changed)
            bb = self.goal_region.bounding_box
            current_goal = crop_region(grid, bb)
            comparison = compare_regions(
                PatternRegion(..., pattern=current_goal, ...),
                self.reference_region
            )
            self.similarity_history.append(comparison.similarity)

            # Phase logic
            if comparison.similarity >= 0.9:
                self.phase = "finish"  # Goal matches reference — go touch it
            elif comparison.similarity > self.similarity_history[0] if self.similarity_history else 0:
                self.phase = "intermediate"  # Making progress — keep visiting intermediates
            else:
                self.phase = "intermediate"  # Not matching yet

            return {
                "phase": self.phase,
                "similarity": comparison.similarity,
                "similarity_trend": self._trend(),
                "reference_location": self.reference_region.location_hint,
                "goal_location": self.goal_region.location_hint,
            }

        return {"phase": "discover", "similarity": 0.0}

    def _trend(self) -> str:
        if len(self.similarity_history) < 2:
            return "unknown"
        if self.similarity_history[-1] > self.similarity_history[-2]:
            return "improving"
        elif self.similarity_history[-1] < self.similarity_history[-2]:
            return "regressing"
        return "stable"
```

### Part 3: Phase-aware autopilot targeting (`orchestrator.py`)

#### 3a. Extend `_try_autopilot()` with phase awareness

Replace the simple "drive to goal" logic with phase-aware targeting:

```python
def _try_autopilot(self, observation, available_actions):
    # ... existing confidence checks ...

    # B167: Phase-aware targeting
    pattern_state = self._pattern_tracker.update(observation.get("grid", []), step)

    if pattern_state["phase"] == "finish":
        # Goal matches reference — navigate to goal and interact
        target = goal_info
        rationale_prefix = "autopilot[finish]: goal matches reference"
    elif pattern_state["phase"] == "intermediate":
        # Find nearest intermediate object to navigate to
        intermediates = [
            r for r in roles.values()
            if r.get("role") == "intermediate" and r.get("estimated_position")
        ]
        if intermediates:
            # Pick the nearest unvisited intermediate
            target = self._nearest_unvisited_intermediate(player_info, intermediates)
            rationale_prefix = "autopilot[intermediate]: visiting interactive object"
        else:
            # No intermediates found — fall back to exploring
            return None  # Let LLM decide
    else:
        # Discover phase — explore to find regions
        return None  # Let LLM explore

    # ... existing direction computation using target ...
```

#### 3b. Track visited intermediates

```python
self._visited_intermediates: Set[tuple[float, float]] = set()

def _nearest_unvisited_intermediate(self, player_info, intermediates):
    """Find the closest intermediate object the player hasn't visited yet."""
    unvisited = [
        i for i in intermediates
        if (round(i["estimated_position"]["row"]), round(i["estimated_position"]["col"]))
           not in self._visited_intermediates
    ]
    if not unvisited:
        # All visited — revisit the nearest (patterns may need multiple visits)
        unvisited = intermediates

    # Sort by Manhattan distance to player
    def dist(i):
        pos = i["estimated_position"]
        return abs(pos["row"] - player_info["row"]) + abs(pos["col"] - player_info["col"])

    return min(unvisited, key=dist)
```

#### 3c. Auto-interact when reaching an intermediate

When the player arrives within 1 cell of an intermediate target:

```python
if abs(dr) <= 1.0 and abs(dc) <= 1.0:
    # Arrived at target
    if pattern_state["phase"] == "intermediate":
        # Mark as visited and interact
        self._visited_intermediates.add(
            (round(target["row"]), round(target["col"]))
        )
        action_id = "ACTION5"  # interact
        rationale = f"{rationale_prefix}, arrived — trying interact"
    elif pattern_state["phase"] == "finish":
        action_id = "ACTION5"  # interact to complete level
        rationale = f"{rationale_prefix}, arrived at goal — interacting to complete"
```

#### 3d. Add pattern match progress to trace

```python
self._emit_trace_event("operation", "pattern_match_progress", {
    "step": step_num,
    "phase": pattern_state["phase"],
    "similarity": pattern_state["similarity"],
    "trend": pattern_state.get("similarity_trend", "unknown"),
})
```

### Part 4: Save and recall puzzle model via SideQuests (`orchestrator.py`)

This is the core memory loop — the agent learns the puzzle structure on level 1 and recalls it on level 2+.

#### 4a. Build a structured puzzle model after each level

After a level ends (win or fail), build a structured understanding of the puzzle:

```python
def _build_puzzle_model(self) -> dict:
    """B167: Build a structured puzzle model from what the agent learned this level."""
    model = {
        "type": "puzzle_model",
        "game_id": self._game_id,
        "level": self._current_level,
        "grid_structure": {
            "reference_location": None,  # e.g., "bottom_left_corner"
            "goal_location": None,       # e.g., "top_right"
            "intermediate_count": 0,
            "intermediate_type": None,   # e.g., "yellow_bordered_markers"
        },
        "mechanic": {
            "description": "",           # e.g., "Visit intermediate markers to transform goal pattern to match reference"
            "visit_order_matters": False,
            "interact_required": True,   # ACTION5 needed at intermediates
        },
        "learned_facts": [],             # Accumulated observations
        "pattern_similarity_at_start": 0.0,
        "pattern_similarity_at_end": 0.0,
        "outcome": "unknown",
    }

    # Populate from PatternMatchTracker
    if self._pattern_tracker:
        if self._pattern_tracker.reference_region:
            model["grid_structure"]["reference_location"] = self._pattern_tracker.reference_region.location_hint
        if self._pattern_tracker.goal_region:
            model["grid_structure"]["goal_location"] = self._pattern_tracker.goal_region.location_hint
        if self._pattern_tracker.similarity_history:
            model["pattern_similarity_at_start"] = self._pattern_tracker.similarity_history[0]
            model["pattern_similarity_at_end"] = self._pattern_tracker.similarity_history[-1]

    # Count intermediates from object roles
    roles = (self._solve_context or {}).get("object_roles", {})
    intermediates = [r for r in roles.values() if r.get("role") == "intermediate"]
    model["grid_structure"]["intermediate_count"] = len(intermediates)

    # Build mechanic description from what happened
    visited = len(self._visited_intermediates)
    if visited > 0:
        model["mechanic"]["description"] = (
            f"Navigate to {len(intermediates)} intermediate markers and interact (ACTION5). "
            f"Each visit transforms the goal pattern. When goal matches reference, "
            f"interact with goal to complete level."
        )

    # Add learned facts from step history
    for step in self._step_history:
        delta = step.get("frame_delta", {})
        if step.get("action_id") == "ACTION5" and delta.get("n_cells_changed", 0) > 10:
            model["learned_facts"].append({
                "fact": f"ACTION5 at step {step['step']} caused {delta['n_cells_changed']} pixel change",
                "interpretation": "interact triggered a state change (possibly transformed goal pattern)",
            })

    return model
```

#### 4b. Save puzzle model to SideQuests after each level

In the level transition handler (when WIN is detected or level ends):

```python
async def _save_puzzle_model(self, outcome: str):
    """B167: Persist puzzle understanding to SideQuests for cross-level recall."""
    model = self._build_puzzle_model()
    model["outcome"] = outcome

    # Save as a structured lesson via report_outcome
    description = (
        f"Level {self._current_level} puzzle model: "
        f"reference at {model['grid_structure']['reference_location']}, "
        f"{model['grid_structure']['intermediate_count']} intermediates, "
        f"mechanic: {model['mechanic']['description']}"
    )

    await self.brain.report_outcome(
        plan_id=None,  # Not tied to a specific plan
        session_id=self.session_id,
        outcome_text=description,
        valence=1.0 if outcome == "solved" else -0.3,
        evidence=model,
    )

    # Also save as a notify_turn so it appears in the conversation history
    await self.brain.notify_turn(
        role="assistant",
        content=f"[PUZZLE MODEL] {description}",
        session_id=self.session_id,
    )

    self._emit_trace_event("operation", "puzzle_model_saved", {
        "level": self._current_level,
        "outcome": outcome,
        "intermediate_count": model["grid_structure"]["intermediate_count"],
        "similarity_start": model["pattern_similarity_at_start"],
        "similarity_end": model["pattern_similarity_at_end"],
    })
```

#### 4c. Recall puzzle model on level 2+

At the start of each new level (in the perceive phase), check for a saved puzzle model:

```python
async def _recall_puzzle_model(self) -> Optional[dict]:
    """B167: Recall saved puzzle understanding from earlier levels."""
    if self._current_level <= 1:
        return None  # Nothing to recall on level 1

    results = await self.brain.current_truth(
        query="puzzle model reference pattern intermediate markers",
        session_id=self.session_id,
        scope="branch",
        limit=3,
    )

    if not results:
        return None

    # Parse the most relevant result
    # The saved model tells us: reference location, intermediate count, mechanic
    self._emit_trace_event("operation", "puzzle_model_recalled", {
        "level": self._current_level,
        "results_count": len(results) if isinstance(results, list) else 1,
    })

    # Apply recalled knowledge:
    # 1. Skip discover phase — go straight to intermediate
    if self._pattern_tracker:
        self._pattern_tracker.phase = "intermediate"

    # 2. Set expected reference location
    # 3. Set expected intermediate count
    return results
```

#### 4d. Progressive model improvement

Each level adds to the puzzle model. The agent doesn't just recall level 1's model — it improves it:

- Level 1: "There are markers to visit" (basic understanding)
- Level 2: "Markers are yellow-bordered, reference is bottom-left" (visual details)
- Level 3: "There are 3 markers per level, visiting each transforms one section of the goal" (quantified)
- Level 4+: "White gates need ACTION5 to open" (new mechanic discovered)

Each level's `_save_puzzle_model()` captures new observations. `recall_plans` retrieves ALL saved models, giving the agent a cumulative understanding.

### Part 5: Tests

```python
class TestRegionComparison:
    def test_exact_match(self):
        # Two identical 3x3 patterns
        a = PatternRegion(pattern=[[1,2,1],[2,0,2],[1,2,1]], ...)
        b = PatternRegion(pattern=[[1,2,1],[2,0,2],[1,2,1]], ...)
        result = compare_regions(a, b)
        assert result.exact_match is True
        assert result.similarity == 1.0

    def test_partial_match(self):
        a = PatternRegion(pattern=[[1,2,1],[2,0,2],[1,2,1]], ...)
        b = PatternRegion(pattern=[[1,2,1],[2,3,2],[1,2,1]], ...)  # One cell different
        result = compare_regions(a, b)
        assert 0.8 < result.similarity < 1.0

    def test_color_shifted_match(self):
        a = PatternRegion(pattern=[[1,2],[2,1]], ...)
        b = PatternRegion(pattern=[[3,4],[4,3]], ...)  # Same structure, different colors
        result = compare_regions(a, b, allow_color_shift=True)
        assert result.similarity == 1.0
        assert result.color_shift == {1: 3, 2: 4}

    def test_no_match(self):
        a = PatternRegion(pattern=[[1,1],[1,1]], ...)
        b = PatternRegion(pattern=[[2,3],[4,5]], ...)
        result = compare_regions(a, b)
        assert result.similarity < 0.5

class TestReferenceGoalPairing:
    def test_corner_region_is_reference(self):
        # Region in bottom-left corner vs region in center
        regions = [
            PatternRegion(location_hint="corner_bl", size=9, ...),
            PatternRegion(location_hint="center", size=9, ...),
        ]
        ref, goal = find_reference_goal_pair(regions, 64, 64)
        assert ref.location_hint == "corner_bl"
        assert goal.location_hint == "center"

class TestPhaseAwareAutopilot:
    def test_finish_phase_targets_goal(self):
        # When similarity >= 0.9, autopilot targets the goal
        ...

    def test_intermediate_phase_targets_nearest_intermediate(self):
        # When similarity < 0.8 and intermediates exist, targets nearest
        ...

    def test_discover_phase_returns_none(self):
        # When no reference/goal pair found, returns None (LLM decides)
        ...

    def test_auto_interact_at_intermediate(self):
        # When arriving at intermediate, tries ACTION5
        ...

    def test_visited_intermediates_skipped(self):
        # Already-visited intermediates are deprioritized
        ...

class TestPuzzleModelMemory:
    def test_build_puzzle_model_captures_structure(self):
        # After a level, model includes reference location, intermediate count, mechanic
        ...

    def test_save_puzzle_model_calls_report_outcome(self):
        # Verify report_outcome is called with structured evidence
        ...

    def test_recall_puzzle_model_skips_discover_phase(self):
        # On level 2+, recalled model sets phase to "intermediate" immediately
        ...

    def test_progressive_model_adds_new_facts(self):
        # Level 2 model includes facts from both level 1 and level 2
        ...

    def test_no_recall_on_level_1(self):
        # Level 1 returns None from recall (nothing saved yet)
        ...
```

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/grid_analysis.py` | Add `PatternRegion` dataclass, `RegionComparison` dataclass, `extract_pattern_regions()`, `compare_regions()`, `find_reference_goal_pair()`, `crop_region()` (~150 lines) |
| `agents/arc3/solver.py` | Add "intermediate" role classification in `ObjectRoleMapper._classify_role()`, add `PatternMatchTracker` class (~80 lines) |
| `agents/arc3/orchestrator.py` | Extend `_try_autopilot()` with phase-aware targeting (~60 lines), add `_pattern_tracker` init, add `_visited_intermediates` tracking, add `_nearest_unvisited_intermediate()`, add `_build_puzzle_model()`, `_save_puzzle_model()`, `_recall_puzzle_model()` (~120 lines) |
| `tests/test_b167_pattern_strategy.py` | New: ~20 tests covering region comparison, pairing, phase logic, autopilot targeting, puzzle model save/recall |

## API/Schema/Test Updates

- No schema changes
- No tool changes
- No adapter changes

## Acceptance Criteria

1. `compare_regions()` returns correct similarity for exact, partial, color-shifted, and no-match cases
2. `find_reference_goal_pair()` correctly identifies corner regions as references
3. Small interactive objects get "intermediate" role (not "decoration")
4. Autopilot navigates to intermediates first when pattern similarity < 0.8
5. Autopilot navigates to goal when pattern similarity ≥ 0.9
6. Pattern similarity is tracked per-step and logged in trace
7. ACTION5 is automatically tried when arriving at an intermediate object
8. Graceful fallback: if region pairing fails, existing autopilot behavior (drive to goal) is preserved
9. All existing tests pass: `pytest tests/test_arc3_*.py -q`

## Validation Commands

```bash
pytest tests/test_b167_pattern_strategy.py -v
pytest tests/test_arc3_orchestrator.py tests/test_arc3_solver.py tests/test_b166_deterministic_autopilot.py -q
```

## Risks / Constraints

- **Region extraction heuristics may fail on some puzzles**: Not all puzzles have clear reference/goal pairs. The fallback (existing autopilot or LLM) handles this gracefully.
- **"Intermediate" classification is probabilistic**: Some decorations really are just decoration. The intermediate role requires evidence of interactivity (the object changed or something changed when the player was near it). False positives waste steps but don't break anything.
- **Pattern comparison is O(cells)**: For 64x64 grids this is ~4096 operations per comparison — negligible.
- **Multiple intermediate objects may need ordering**: Some puzzles may require visiting intermediates in a specific order. For now, "nearest first" is good enough. Order-sensitive puzzles would need a separate card.
- **Color-shifted matching adds complexity**: Some puzzles transform colors between reference and goal. The color-shift detection handles this but may have edge cases with many colors.
