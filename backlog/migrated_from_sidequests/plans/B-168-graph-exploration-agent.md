# B-168: Graph-Based Exploration Agent — Implementation Plan

**Card**: B168
**Priority**: P0
**Dependencies**: B150 (GridDiffEngine), B119 (bootstrap entity discovery), B166 (autopilot)

## Summary

Add a two-phase exploration agent that builds a graph-based knowledge substrate in KuzuDB before the main action loop. Phase 1 performs static structural analysis of the initial grid (no actions consumed). Phase 2 uses a deterministic action sweep followed by LLM-guided follow-up to discover behavioral relationships. The resulting graph feeds high-confidence entity roles to the existing ObjectRoleMapper.

## Technical Approach

### 1. Schema Additions — `mcp_engine/schema.py`

Add to `NODE_TABLES`:

```python
"GridEntity": """
    entity_id         STRING,
    task_id           STRING,
    level             INT32,
    color_id          INT32,
    region_index      INT32,
    pixel_count       INT32,
    centroid_row      DOUBLE,
    centroid_col      DOUBLE,
    bbox_min_row      INT32,
    bbox_min_col      INT32,
    bbox_max_row      INT32,
    bbox_max_col      INT32,
    location_hint     STRING,
    aspect_ratio      DOUBLE,
    compactness       DOUBLE,
    is_background     BOOLEAN,
    is_mobile         BOOLEAN,
    is_interactive    BOOLEAN,
    inferred_role     STRING,
    role_confidence   DOUBLE,
    last_updated_step INT32,
    created_at        TIMESTAMP,
    PRIMARY KEY (entity_id)
""",

"GridSnapshot": """
    snapshot_id       STRING,
    task_id           STRING,
    level             INT32,
    step              INT32,
    grid_hash         STRING,
    rows              INT32,
    cols              INT32,
    n_entities        INT32,
    symmetry_axes     STRING[],
    created_at        TIMESTAMP,
    PRIMARY KEY (snapshot_id)
""",

"ActionEffect": """
    effect_id         STRING,
    task_id           STRING,
    level             INT32,
    action_id         STRING,
    step              INT32,
    n_cells_changed   INT32,
    apparent_effect   STRING,
    direction_row     DOUBLE,
    direction_col     DOUBLE,
    created_at        TIMESTAMP,
    PRIMARY KEY (effect_id)
""",
```

Add to `REL_TABLES`:

```python
"CREATE REL TABLE IF NOT EXISTS OBSERVED_IN (FROM GridEntity TO GridSnapshot, step INT32)",
"CREATE REL TABLE IF NOT EXISTS ADJACENT_TO (FROM GridEntity TO GridEntity, min_distance DOUBLE, direction STRING, step INT32)",
"CREATE REL TABLE IF NOT EXISTS STRUCTURALLY_SIMILAR (FROM GridEntity TO GridEntity, similarity DOUBLE, color_shifted BOOLEAN, step INT32)",
"CREATE REL TABLE IF NOT EXISTS SAME_COLOR_AS (FROM GridEntity TO GridEntity)",
"CREATE REL TABLE IF NOT EXISTS CONTAINS_ENTITY (FROM GridEntity TO GridEntity, step INT32)",
"CREATE REL TABLE IF NOT EXISTS MOVED_BY (FROM GridEntity TO ActionEffect, delta_row DOUBLE, delta_col DOUBLE)",
"CREATE REL TABLE IF NOT EXISTS RESPONDS_TO (FROM GridEntity TO ActionEffect, effect_type STRING)",
"CREATE REL TABLE IF NOT EXISTS BLOCKS (FROM GridEntity TO GridEntity, action_id STRING, step INT32)",
"CREATE REL TABLE IF NOT EXISTS ENTITY_HYPOTHESIS (FROM GridEntity TO Hypothesis, weight FLOAT, step INT32)",
```

### 2. New File — `agents/arc3/entity_graph.py` (~500 lines)

```python
class EntityGraphBuilder:
    """Builds a graph-based knowledge substrate for ARC puzzle entities."""

    def __init__(self, db: KuzuClient, task_id: str):
        self.db = db
        self.task_id = task_id
        self._entities: Dict[str, dict] = {}  # entity_id -> properties (local cache)
        self._pending_llm_inference: Optional[asyncio.Task] = None  # background LLM task

    # ── Phase 1: Static Analysis ────────────────────────────────────

    async def bootstrap(self, grid, level, observation) -> dict:
        """Step 0: Extract all entities and structural relationships.
        No actions consumed. Returns summary dict."""

        # 1. Extract connected components via GridDiffEngine
        diff_engine = GridDiffEngine()
        components = diff_engine.extract_connected_components(grid, color=-1)
        pattern_regions = diff_engine.extract_pattern_regions(grid)
        symmetry = diff_engine.detect_symmetry(grid)
        total_pixels = len(grid) * len(grid[0])

        # 2. Create GridSnapshot node
        snapshot_id = f"{self.task_id}_{level}_step0"
        # ... MERGE GridSnapshot ...

        # 3. Create GridEntity nodes (one per connected region)
        for idx, comp in enumerate(components):
            entity_id = f"{self.task_id}_{level}_{comp.color}_{idx}"
            bbox = comp.bounding_box
            bbox_area = max((bbox[2]-bbox[0]+1) * (bbox[3]-bbox[1]+1), 1)
            props = {
                "entity_id": entity_id,
                "task_id": self.task_id,
                "level": level,
                "color_id": comp.color,
                "region_index": idx,
                "pixel_count": comp.size,
                "centroid_row": sum(r for r,c in comp.cells) / comp.size,
                "centroid_col": sum(c for r,c in comp.cells) / comp.size,
                "bbox_min_row": bbox[0], "bbox_min_col": bbox[1],
                "bbox_max_row": bbox[2], "bbox_max_col": bbox[3],
                "location_hint": _compute_location_hint(bbox, len(grid), len(grid[0])),
                "aspect_ratio": (bbox[3]-bbox[1]+1) / max(bbox[2]-bbox[0]+1, 1),
                "compactness": comp.size / bbox_area,
                "is_background": comp.color == 0 or comp.size > total_pixels * 0.5,
                "is_mobile": False,
                "is_interactive": False,
                "inferred_role": "unknown",
                "role_confidence": 0.0,
            }
            # MERGE node, create OBSERVED_IN edge
            self._entities[entity_id] = props

        # 4. Create structural relationships
        # ADJACENT_TO: pairwise bbox distance <= 2
        # STRUCTURALLY_SIMILAR: compare_regions() >= 0.5
        # CONTAINS_ENTITY: smaller entity bbox fully inside larger
        # SAME_COLOR_AS: same color_id

        return {"n_entities": len(self._entities), "symmetry": symmetry}

    # ── Phase 2a: Record Action Effect ──────────────────────────────

    async def record_action_effect(self, grid_before, grid_after, action_id, step, level):
        """After one action: record what changed, then run inference."""

        diff_engine = GridDiffEngine()
        delta = diff_engine.diff_frames(grid_before, grid_after, action_id)

        # Create ActionEffect node
        effect_id = f"{self.task_id}_{level}_{action_id}_{step}"
        # MERGE ActionEffect node with delta.n_cells_changed, delta.apparent_effect, delta.direction

        # Match entities across frames by color + centroid proximity
        comps_after = diff_engine.extract_connected_components(grid_after, color=-1)
        # For each entity: check if centroid shifted > 0.35
        #   Shifted → MOVED_BY edge, set is_mobile = true
        #   Appeared/disappeared → RESPONDS_TO edge
        #   Other entity blocked by static → BLOCKS edge

        # ── Run dual inference after recording ──
        inference_result = await self.run_inference(step)
        return inference_result

    # ══════════════════════════════════════════════════════════════════
    # DUAL INFERENCE ENGINE
    #
    # Two tracks run after each exploration step:
    #   Foreground (blocking, fast): deterministic rule propagation
    #   Background (non-blocking, deep): LLM causal reasoning
    #
    # The key insight: each observation teaches us about MORE than one
    # entity. If color 11 moved right on ACTION4, we also learn about
    # every entity structurally similar to 11, every entity that DIDN'T
    # move, and every entity adjacent to 11's path.
    #
    # The deterministic rules catch obvious patterns instantly.
    # The LLM catches non-obvious causal chains (e.g. "player moved →
    # health bar shrank → these are causally linked") in the background.
    # ══════════════════════════════════════════════════════════════════

    async def run_inference(self, step: int) -> "InferenceResult":
        """Run deterministic inference (blocking) + kick off LLM inference (background)."""

        # ── Foreground: Deterministic rules (instant) ──
        tier1_changes = await self._tier1_similarity_propagation(step)
        tier2_changes = await self._tier2_relational_inference(step)
        tier3_changes = await self._tier3_role_elimination(step)

        # ── Background: LLM causal inference (non-blocking) ──
        # Kick off async — does NOT block the exploration loop.
        # Results merge into graph whenever they arrive.
        self._kick_off_llm_inference(step)

        # ── Compute exploration frontier ──
        frontier = await self._get_exploration_frontier()

        return InferenceResult(
            entities_updated=tier1_changes + tier2_changes + tier3_changes,
            frontier_size=len(frontier),
            frontier=frontier,
        )

    # ── Tier 1: Similarity Propagation (deterministic) ──────────────
    #
    # If entity A moved, propagate is_mobile to all STRUCTURALLY_SIMILAR
    # and SAME_COLOR_AS entities with decayed confidence.
    # If entity A did NOT change, increment stationary evidence.

    async def _tier1_similarity_propagation(self, step: int) -> int:
        """Propagate behavioral properties through similarity edges."""
        changes = 0

        # Propagate is_mobile from observed → structurally similar
        # Cypher:
        #   MATCH (observed:GridEntity)-[s:STRUCTURALLY_SIMILAR]->(similar:GridEntity)
        #   WHERE observed.task_id = $task_id
        #     AND observed.is_mobile = true
        #     AND similar.is_mobile = false
        #     AND similar.role_confidence < 0.5
        #     AND s.similarity >= 0.7
        #   SET similar.is_mobile = true,
        #       similar.role_confidence = observed.role_confidence * s.similarity

        # Propagate is_mobile from observed → same color
        # Cypher:
        #   MATCH (observed:GridEntity)-[:SAME_COLOR_AS]->(other:GridEntity)
        #   WHERE observed.task_id = $task_id
        #     AND observed.is_mobile = true
        #     AND other.is_mobile = false
        #   SET other.is_mobile = true,
        #       other.role_confidence = observed.role_confidence * 0.8

        # Negative evidence: entities NOT in any MOVED_BY for this step
        # get stationary reinforcement
        return changes

    # ── Tier 2: Relational Inference (deterministic) ────────────────
    #
    # Blocking: If A tried to move toward B but didn't → BLOCKS edge.
    # Co-movement: If A and B both moved with similar deltas → CO_MOVES_WITH.
    # Co-occurrence: If A moved AND B changed simultaneously → CORRELATES_WITH
    #   (deterministic: we flag the co-occurrence, LLM later explains causation).

    async def _tier2_relational_inference(self, step: int) -> int:
        """Infer relationships from behavioral co-occurrence."""
        changes = 0

        # Blocking inference:
        #   For each entity with a MOVED_BY edge this step,
        #   check if movement was less than expected.
        #   If entity in front (ADJACENT_TO, matching direction) didn't move → BLOCKS

        # Co-movement:
        #   MATCH (a:GridEntity)-[ma:MOVED_BY]->(effect:ActionEffect {step: $step}),
        #         (b:GridEntity)-[mb:MOVED_BY]->(effect)
        #   WHERE a <> b AND abs(ma.delta_row - mb.delta_row) < 1
        #     AND abs(ma.delta_col - mb.delta_col) < 1
        #   MERGE (a)-[:CO_MOVES_WITH {step: $step}]->(b)

        # Co-occurrence (the KEY pattern for non-obvious relationships):
        #   Find all entity pairs where BOTH changed on the same step
        #   but one moved (MOVED_BY) and the other responded differently (RESPONDS_TO).
        #   This is the "player moved → health bar shrank" pattern.
        #
        #   MATCH (mover:GridEntity)-[:MOVED_BY]->(effect:ActionEffect {step: $step}),
        #         (reactor:GridEntity)-[:RESPONDS_TO]->(effect)
        #   WHERE mover <> reactor
        #   MERGE (mover)-[:CORRELATES_WITH {step: $step, mechanism: 'unknown'}]->(reactor)
        #
        #   The mechanism stays 'unknown' until the LLM (Tier 4) explains it.

        return changes

    # ── Tier 3: Role Elimination (constraint propagation) ───────────
    #
    # Once a role is confirmed (conf > 0.7), constrain remaining entities.
    # Player identified → no other mobile entity is player (enemy/companion).
    # Wall identified → all STRUCTURALLY_SIMILAR unknowns become walls.
    # This narrows the exploration frontier fast.

    async def _tier3_role_elimination(self, step: int) -> int:
        """Eliminate impossible roles based on confirmed assignments."""
        changes = 0

        # If player confirmed: other mobile entities → "not_player" constraint
        # If wall confirmed: similar unknowns → wall (propagate via STRUCTURALLY_SIMILAR)
        # If background confirmed: similar large entities → background

        # After elimination, count remaining unknowns with role_confidence < 0.5
        # This is the exploration frontier — entities that still need testing
        return changes

    # ── Tier 4: LLM Causal Inference (background, non-blocking) ────
    #
    # The deterministic rules above catch obvious patterns. But puzzles
    # have non-obvious causal chains: player moves → health bar drops,
    # switch activates → gate opens, marker visited → pattern changes.
    #
    # The LLM examines CORRELATES_WITH edges (from Tier 2) and the full
    # graph context to EXPLAIN the mechanism and create rich edges.
    #
    # This runs in the background — it does NOT block the exploration
    # loop or the main agent loop. Results merge into the graph whenever
    # they arrive, enriching future inferences.

    def _kick_off_llm_inference(self, step: int):
        """Launch background LLM inference task (non-blocking)."""
        # Cancel any previous pending inference
        if self._pending_llm_inference and not self._pending_llm_inference.done():
            self._pending_llm_inference.cancel()

        self._pending_llm_inference = asyncio.create_task(
            self._llm_causal_inference(step)
        )

    async def _llm_causal_inference(self, step: int):
        """Background: LLM examines graph for non-obvious relationships.

        Looks at CORRELATES_WITH edges where mechanism='unknown' and asks
        the LLM to explain the causal relationship.

        Example prompt context:
          "Step 3: Entity 'player' (color 11, 4px) moved right via ACTION4.
           Entity 'health_indicator' (color 14, 12px) lost 2 pixels simultaneously.
           These co-occurred but share no known relationship.
           What is the likely causal mechanism?"

        LLM response → creates/updates edge:
          (player)-[:CAUSES_CHANGE_IN {mechanism: "movement_costs_health",
                                        confidence: 0.8}]->(health_indicator)
        """
        try:
            # 1. Query graph for unexplained CORRELATES_WITH edges
            #    MATCH (a)-[c:CORRELATES_WITH {mechanism: 'unknown'}]->(b)
            #    WHERE a.task_id = $task_id
            #    RETURN a, b, c

            # 2. Build context from entity properties + graph neighborhood
            #    Include: what each entity looks like, where it is, what it does,
            #    what happened on the step that created the correlation

            # 3. Call LLM with causal reasoning prompt
            #    "Given these co-occurring changes, what relationship exists?"

            # 4. Parse LLM response → update edge mechanism + create CAUSES_CHANGE_IN
            #    Also create any new edges the LLM identifies (e.g. "this entity
            #    is probably a score counter because it decrements on every move")

            # 5. If LLM identifies a role (e.g. "this is a health bar"),
            #    update GridEntity.inferred_role and role_confidence
            pass
        except asyncio.CancelledError:
            pass  # Superseded by newer inference
        except Exception as e:
            logger.warning("B168: LLM inference failed (non-fatal): %s", e)

    # ── Exploration Frontier ────────────────────────────────────────
    #
    # After each inference pass, compute what's still unknown.
    # This tells the exploration loop what to target next.
    # If inference collapses the frontier to 0, exploration can end early.

    async def _get_exploration_frontier(self) -> List[dict]:
        """Return entities that still need investigation."""
        # Cypher:
        #   MATCH (e:GridEntity)
        #   WHERE e.task_id = $task_id
        #     AND e.is_background = false
        #     AND e.role_confidence < 0.5
        #     AND e.inferred_role = 'unknown'
        #   RETURN e.entity_id, e.color_id, e.pixel_count
        #   ORDER BY e.pixel_count ASC
        return []

    # ── Phase 2b: Exploration Summary for LLM ───────────────────────

    async def get_exploration_summary(self) -> dict:
        """Return structured summary of what's known for LLM follow-up."""
        # Collect any background LLM inference results first
        if self._pending_llm_inference and not self._pending_llm_inference.done():
            try:
                await asyncio.wait_for(self._pending_llm_inference, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass  # Don't block — use what we have

        # Query graph for: mobile entities, static entities, unexplored actions,
        # unclear relationships, structural groups, unexplained correlations
        frontier = await self._get_exploration_frontier()
        return {
            "mobile_entities": [],       # entities with MOVED_BY edges
            "static_entities": [],       # entities with stationary evidence
            "causal_chains": [],         # CAUSES_CHANGE_IN edges (from LLM)
            "unexplained_correlations": [],  # CORRELATES_WITH where mechanism='unknown'
            "structural_groups": [],     # clusters of STRUCTURALLY_SIMILAR entities
            "exploration_frontier": frontier,  # what still needs testing
        }

    # ── Role Inference Queries ──────────────────────────────────────

    async def infer_player(self) -> Optional[dict]:
        """Most mobile non-background entity."""
        # Cypher: MATCH (e:GridEntity)-[m:MOVED_BY]->(a:ActionEffect)
        # WHERE e.is_background = false
        # RETURN e ORDER BY count(m) DESC, e.pixel_count ASC LIMIT 1

    async def infer_goal(self) -> Optional[dict]:
        """Stationary entity in player's movement direction."""
        # Cypher: stationary + structurally unique + in direction of player movement

    async def infer_walls(self) -> List[dict]:
        """Large, elongated, never-moving entities."""
        # Cypher: is_background=false AND is_mobile=false AND pixel_count>50
        # AND (aspect_ratio < 0.2 OR aspect_ratio > 5.0)

    async def infer_intermediates(self) -> List[dict]:
        """Small, stationary, structurally similar entities."""
        # Cypher: MATCH (e)-[:STRUCTURALLY_SIMILAR]->(other)
        # WHERE is_mobile=false AND pixel_count < 30

    async def get_entity_roles(self) -> Dict[int, ObjectRole]:
        """Aggregate all inferences into ObjectRole dict keyed by color_id."""
        # Calls infer_player/goal/walls/intermediates
        # Returns Dict[int, ObjectRole] compatible with SolveEngine._merge_persistent_roles

    # ── Cleanup ─────────────────────────────────────────────────────

    async def cleanup(self, task_id, level):
        """Remove ephemeral entity/snapshot/effect nodes after puzzle done."""
        # DELETE GridEntity, GridSnapshot, ActionEffect WHERE task_id = $task_id
```

### Inference Data Flow

```
Step N action executed
        │
        ▼
record_action_effect()     ← create ActionEffect node + MOVED_BY/RESPONDS_TO edges
        │
        ▼
run_inference()
        │
        ├──► Tier 1 (blocking): Similarity propagation
        │    "color 11 moved → similar entities probably move too"
        │    STRUCTURALLY_SIMILAR + SAME_COLOR_AS → propagate is_mobile
        │
        ├──► Tier 2 (blocking): Relational inference
        │    "color 11 moved AND color 14 shrank → CORRELATES_WITH"
        │    Blocking, co-movement, co-occurrence detection
        │
        ├──► Tier 3 (blocking): Role elimination
        │    "player confirmed → no other entity is player"
        │    Constraint propagation narrows remaining unknowns
        │
        └──► Tier 4 (background): LLM causal inference
             "CORRELATES_WITH mechanism='unknown' → ask LLM to explain"
             Non-blocking. Creates CAUSES_CHANGE_IN edges when done.
             Results enrich the graph for future steps.
        │
        ▼
_get_exploration_frontier()
        │
        ▼
"3 entities still unknown" → target these in Phase 2b LLM follow-up
```

### New Relationship Types for Inference

Add to `REL_TABLES` (in addition to those in section 1):

```python
"CREATE REL TABLE IF NOT EXISTS CO_MOVES_WITH (FROM GridEntity TO GridEntity, step INT32)",
"CREATE REL TABLE IF NOT EXISTS CORRELATES_WITH (FROM GridEntity TO GridEntity, step INT32, mechanism STRING)",
"CREATE REL TABLE IF NOT EXISTS CAUSES_CHANGE_IN (FROM GridEntity TO GridEntity, mechanism STRING, confidence DOUBLE, step INT32)",
```

### 3. Runner Integration — `agents/arc3/runner.py`

In `_run_puzzle()`, between perceive/plan (line ~222) and the main while loop (line ~228):

```python
# After: await orchestrator.plan(observation, memory_context)
# Before: while steps_this_attempt < max_steps:

# NEW: Exploration phase (B168)
entity_graph = None
if hasattr(self.brain, 'db') and self.brain.db is not None:
    from agents.arc3.entity_graph import EntityGraphBuilder
    entity_graph = EntityGraphBuilder(self.brain.db, task.task_id)

    # Phase 1: Static analysis (no steps consumed)
    grid = observation.get("grid")
    await entity_graph.bootstrap(grid, level=0, observation=observation)

    # Phase 2a: Deterministic sweep (one step per available action)
    available = observation.get("available_actions", [])
    for explore_action_id in available:
        if steps_this_attempt >= max_steps:
            break
        grid_before = observation.get("grid")
        frame_response, reward, done, guid = await self._execute_action(
            game_id, guid, {"action_id": explore_action_id}, total_steps
        )
        observation = adapter.normalize_observation(frame_response)
        grid_after = observation.get("grid")
        await entity_graph.record_action_effect(grid_before, grid_after, explore_action_id, total_steps, level=0)
        orchestrator.record_step_result(reward, done, next_observation=observation)
        steps_this_attempt += 1
        total_steps += 1
        if done:
            break

    # Phase 2b: LLM-guided follow-up (up to 4 steps)
    if not done and steps_this_attempt < max_steps:
        summary = await entity_graph.get_exploration_summary()
        # Build curiosity prompt from summary, get LLM to pick actions
        # ... (up to 4 iterations) ...

    # Handoff: merge graph-inferred roles into orchestrator
    if not done:
        graph_roles = await entity_graph.get_entity_roles()
        orchestrator.merge_graph_roles(graph_roles)

# Existing main loop continues...
```

### 4. Orchestrator Changes — `agents/arc3/orchestrator.py`

Add method:

```python
def merge_graph_roles(self, graph_roles: Dict[int, ObjectRole]):
    """B168: Accept graph-inferred roles from exploration agent.
    Higher confidence wins when merging with existing heuristic roles."""
    if not graph_roles:
        return
    sc = self._solve_context or {}
    existing = sc.get("object_roles") or {}
    for color_id, graph_role in graph_roles.items():
        color_key = str(color_id)
        current = existing.get(color_key)
        if current is None or graph_role.confidence > current.get("confidence", 0):
            existing[color_key] = {
                "role": graph_role.role.value,
                "confidence": graph_role.confidence,
                "estimated_position": graph_role.estimated_position,
            }
    sc["object_roles"] = existing
    self._solve_context = sc
```

### 5. Adapter Changes — `benchmarks/arc3/adapter.py`

Add `db` property to protocol and implementations:

```python
# BrainClientProtocol
@property
def db(self) -> Optional[Any]:
    return None

# NoOpBrainClient
@property
def db(self):
    return None

# LedgerBrainClient
@property
def db(self):
    return None

# LocalBrainClient already has self.db — just verify it's accessible
```

### 6. KuzuDB Cypher Dialect Notes

KuzuDB 0.11.3 Cypher subset considerations:
- Use `MERGE` for idempotent node creation (supported)
- `NOT EXISTS { subquery }` may not be supported — use `OPTIONAL MATCH` + `WHERE x IS NULL` pattern instead
- `abs()` is supported
- `avg()`, `count()`, `sum()` aggregations are supported
- Parameterized queries via `$param_name` are supported
- `STRING[]` array type is supported for `symmetry_axes`

## Step Budget Impact

With 4 available actions typical:
- Phase 1: 0 steps (static analysis only)
- Phase 2a: 4 steps (one per action)
- Phase 2b: ~4 steps (LLM follow-up)
- **Total: ~8 steps** out of 119+ budget (< 7%)

## API/Schema/Test Updates

### Tests — `tests/test_b168_graph_exploration.py`

```python
class TestEntityGraphBootstrap:
    # Test: bootstrap creates correct number of GridEntity nodes
    # Test: background entities (color 0) have is_background=True
    # Test: ADJACENT_TO edges created for nearby entities
    # Test: STRUCTURALLY_SIMILAR edges created for similar regions
    # Test: CONTAINS_ENTITY edges for nested bounding boxes

class TestActionEffectRecording:
    # Test: record_action_effect creates ActionEffect node
    # Test: MOVED_BY edge created when entity centroid shifts
    # Test: is_mobile set to True on moved entity
    # Test: BLOCKS edge when entity doesn't move despite action

class TestRoleInference:
    # Test: infer_player returns most mobile non-background entity
    # Test: infer_player never returns is_background=True entity
    # Test: infer_walls returns large elongated stationary entities
    # Test: infer_intermediates returns small structurally-grouped entities
    # Test: get_entity_roles returns valid ObjectRole dict

class TestNoOpFallback:
    # Test: when db=None, exploration phase is skipped entirely
    # Test: existing heuristic path unchanged when graph unavailable

class TestGraphRoleMerge:
    # Test: merge_graph_roles overrides lower-confidence heuristic roles
    # Test: merge_graph_roles preserves higher-confidence existing roles
```

### Validation Commands

```bash
# Unit tests
pytest tests/test_b168_graph_exploration.py -v

# Regression tests
pytest tests/test_arc3_solver.py tests/test_arc3_orchestrator.py tests/test_b166_deterministic_autopilot.py tests/test_b167_pattern_strategy.py tests/test_b150_grid_diff_engine.py -q

# Adapter/tool tests
pytest tests/test_adapters.py tests/test_analogical.py tests/test_web.py -q

# Smoke test
.venv/bin/python run_single_puzzle.py --live-smoke --model qwen2.5:7b
# Verify: player != color 0, exploration events in trace, graph entities created
```

## Risks and Constraints

1. **KuzuDB Cypher subset**: Some Neo4j Cypher features may not be available in KuzuDB 0.11.3. Test all queries against the actual DB before integration. Fall back to simpler patterns if needed.
2. **Entity identity across frames**: Matching "same entity at step N and N+1" uses color + centroid proximity (threshold: 5 cells). This may fail if entities teleport or split. Acceptable for initial implementation.
3. **Graph cleanup**: Each puzzle creates N entities + snapshots + effects. `cleanup()` must run after puzzle completion to prevent graph bloat.
4. **Async context**: All graph writes go through `db.execute_write()` (async with lock). The runner already operates in async context so this fits naturally.
5. **NoOp compatibility**: When `brain.db` is None, the entire exploration phase is skipped. Zero impact on baseline benchmarks.
