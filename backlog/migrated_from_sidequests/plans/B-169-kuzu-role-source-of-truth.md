# Plan for B169 — KuzuDB as Single Source of Truth for Object Roles

## Card Metadata

- **Card ID**: B169
- **Priority**: P0
- **Dependencies**: B168 (graph-based exploration agent)

## Summary

`SolveEngine._object_roles` is an in-memory dict that competes with KuzuDB's `GridEntity` nodes as the role store. This plan converts the dict into a read-through cache over KuzuDB. All writers persist to KuzuDB first; the cache syncs once per step.

## Current State (what you're working with)

### Writers to `_object_roles` (5 sites in solver.py)

All in `_merge_persistent_roles()` at lines 2525, 2538, 2558, 2573, 2586:
```python
self._object_roles[color_id] = new_role
```

Plus `_bootstrap_entity_discovery()` in orchestrator.py line 834:
```python
self.solve_engine._object_roles[color_id] = role
```

Plus `merge_graph_roles()` in orchestrator.py line 599:
```python
self.solve_engine._object_roles[color_id] = graph_role
```

### Readers of `_object_roles` (17 sites)

**solver.py** (12 sites): lines 1830, 1833, 2084, 2182, 2229, 2230, 2338, 2479, 2480, 2523, 2604 — all read `self._object_roles`

**orchestrator.py** (5 sites): lines 246, 268, 493, 780, 836 — all read `self.solve_engine._object_roles`

### KuzuDB schema (already exists, no changes needed)

`GridEntity` node (in `mcp_engine/schema.py` line 414) already has:
- `inferred_role STRING` (line 433)
- `role_confidence DOUBLE` (line 434)
- `entity_id` (PK), `task_id`, `level`, `color_id`, plus spatial properties

### Key classes

- `ObjectRole` (solver.py ~line 42): dataclass with `color_id`, `role: RoleType`, `confidence`, `estimated_position`, `evidence_steps`
- `RoleType` (solver.py line 35): enum — PLAYER, GOAL, EXIT, WALL, INTERMEDIATE, UNKNOWN
- `EntityGraphBuilder` (entity_graph.py): already has KuzuDB connection, writes GridEntity nodes
- `KuzuClient` (mcp_engine/graph/kuzu_client.py): async Cypher execution

## Technical Approach

### Step 1: Add role persistence methods to `EntityGraphBuilder`

In `agents/arc3/entity_graph.py`, add two methods:

```python
async def persist_role(self, color_id: int, role: str, confidence: float,
                       position: Optional[Dict[str, float]] = None,
                       level: int = 0) -> None:
    """Write a role assignment to KuzuDB GridEntity node.
    Creates or updates the GridEntity for this color_id."""
    entity_id = f"{self.task_id}_L{level}_c{color_id}"
    # MERGE — create if missing, always update role fields
    await self.db.execute_write(
        """
        MERGE (e:GridEntity {entity_id: $eid})
        ON CREATE SET e.task_id = $tid, e.level = $level, e.color_id = $cid,
                      e.inferred_role = $role, e.role_confidence = $conf,
                      e.centroid_row = $crow, e.centroid_col = $ccol,
                      e.created_at = timestamp($now)
        ON MATCH SET e.inferred_role = $role, e.role_confidence = $conf,
                     e.centroid_row = CASE WHEN $crow IS NOT NULL THEN $crow ELSE e.centroid_row END,
                     e.centroid_col = CASE WHEN $ccol IS NOT NULL THEN $ccol ELSE e.centroid_col END,
                     e.last_updated_step = $step
        """,
        {
            "eid": entity_id, "tid": self.task_id, "level": level,
            "cid": color_id, "role": role, "conf": confidence,
            "crow": position.get("row") if position else None,
            "ccol": position.get("col") if position else None,
            "now": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "step": getattr(self, '_current_step', 0),
        }
    )

async def load_all_roles(self, level: int = 0) -> Dict[int, "ObjectRole"]:
    """Read all roles from KuzuDB GridEntity nodes for this task/level.
    Returns Dict[color_id, ObjectRole] matching the SolveEngine format."""
    from agents.arc3.solver import ObjectRole, RoleType
    rows = await self.db.execute_read(
        """
        MATCH (e:GridEntity)
        WHERE e.task_id = $tid AND e.level = $level
          AND e.inferred_role IS NOT NULL
          AND e.inferred_role <> 'unknown'
        RETURN e.color_id AS color_id, e.inferred_role AS role,
               e.role_confidence AS confidence,
               e.centroid_row AS crow, e.centroid_col AS ccol
        """,
        {"tid": self.task_id, "level": level}
    )
    roles = {}
    for row in rows:
        cid = row["color_id"]
        pos = None
        if row.get("crow") is not None and row.get("ccol") is not None:
            pos = {"row": float(row["crow"]), "col": float(row["ccol"])}
        try:
            role_type = RoleType(row["role"])
        except ValueError:
            role_type = RoleType.UNKNOWN
        roles[cid] = ObjectRole(
            color_id=cid,
            role=role_type,
            confidence=row.get("confidence") or 0.0,
            estimated_position=pos,
        )
    return roles
```

### Step 2: Add `_sync_roles_from_db()` to SolveEngine

In `agents/arc3/solver.py`, add a method to SolveEngine:

```python
async def _sync_roles_from_db(self):
    """Load roles from KuzuDB into the _object_roles cache.
    Called once per step. KuzuDB is authoritative."""
    if not self._entity_graph:
        return  # NoOp mode — keep in-memory-only path
    try:
        db_roles = await self._entity_graph.load_all_roles(level=self._current_level)
        # Replace cache entirely with DB state
        self._object_roles = db_roles
    except Exception as exc:
        logger.warning("B169: _sync_roles_from_db failed, keeping cache: %s", exc)
```

**Important**: SolveEngine needs a reference to the EntityGraphBuilder. Add `self._entity_graph: Optional[EntityGraphBuilder] = None` to `__init__` and set it from the runner.

### Step 3: Make writers persist to KuzuDB

#### 3a. `seed_bootstrap_roles()` in solver.py (ObjectRoleMapper)

After building each `ObjectRole`, call `persist_role()`. Since `seed_bootstrap_roles` is sync and `persist_role` is async, the caller (`_bootstrap_entity_discovery` in orchestrator.py) should handle persistence:

```python
# In orchestrator.py _bootstrap_entity_discovery():
bootstrap_roles = self.solve_engine.role_mapper.seed_bootstrap_roles(observation)
for color_id, role in bootstrap_roles.items():
    existing = self.solve_engine._object_roles.get(color_id)
    if existing is None or existing.role == "unknown" or role.confidence > existing.confidence:
        self.solve_engine._object_roles[color_id] = role  # cache (immediate)
        # Persist to KuzuDB
        if self._entity_graph:
            await self._entity_graph.persist_role(
                color_id, role.role.value, role.confidence,
                role.estimated_position, level=self._current_level
            )
```

#### 3b. `_merge_persistent_roles()` in solver.py

At each of the 5 write sites (lines 2525, 2538, 2558, 2573, 2586), after setting `self._object_roles[color_id] = new_role`, also persist:

```python
self._object_roles[color_id] = new_role
# B169: persist to KuzuDB
if self._entity_graph:
    import asyncio
    asyncio.ensure_future(self._entity_graph.persist_role(
        color_id, new_role.role.value, new_role.confidence,
        new_role.estimated_position, level=self._current_level
    ))
```

**Alternative (cleaner)**: Add a helper `_set_role(color_id, role)` that writes to both cache and KuzuDB, and replace all 5 direct assignments with calls to it:

```python
def _set_role(self, color_id: int, role: ObjectRole):
    """Write role to cache and schedule KuzuDB persistence."""
    self._object_roles[color_id] = role
    if self._entity_graph:
        self._pending_role_writes.append((color_id, role))

async def _flush_role_writes(self):
    """Batch-persist pending role writes to KuzuDB. Called at end of step()."""
    if not self._entity_graph or not self._pending_role_writes:
        return
    for color_id, role in self._pending_role_writes:
        await self._entity_graph.persist_role(
            color_id, role.role.value, role.confidence,
            role.estimated_position, level=self._current_level
        )
    self._pending_role_writes.clear()
```

This is the **recommended approach** — it avoids scattering async calls across merge logic and batches writes.

#### 3c. `merge_graph_roles()` in orchestrator.py

Simplify to just call `persist_role()` — the cache sync will pick it up:

```python
def merge_graph_roles(self, graph_roles: Dict[int, ObjectRole]):
    """B168: Persist graph-inferred roles to KuzuDB. Cache syncs at next step."""
    if not graph_roles or not self._entity_graph:
        return
    for color_id, graph_role in graph_roles.items():
        # Write directly to KuzuDB — _sync_roles_from_db will update cache
        await self._entity_graph.persist_role(
            color_id, graph_role.role.value, graph_role.confidence,
            graph_role.estimated_position, level=self._current_level
        )
    # Also update cache immediately so the current step benefits
    for color_id, graph_role in graph_roles.items():
        existing = self.solve_engine._object_roles.get(color_id)
        if existing is None or graph_role.confidence > existing.confidence:
            self.solve_engine._object_roles[color_id] = graph_role
```

**Note**: `merge_graph_roles` must become `async` since `persist_role` is async. Update the runner.py call site from `orchestrator.merge_graph_roles(...)` to `await orchestrator.merge_graph_roles(...)`.

### Step 4: Wire EntityGraphBuilder into SolveEngine via runner.py

In `agents/arc3/runner.py`, after creating the `EntityGraphBuilder`, pass it to the SolveEngine:

```python
# After entity_graph = EntityGraphBuilder(...)
orchestrator.solve_engine._entity_graph = entity_graph
```

Also add `self._current_level: int = 0` tracking to SolveEngine and update it when levels change.

### Step 5: Add `_sync_roles_from_db()` call at start of step

In the SolveEngine's `step()` method (the main per-step entry point), add at the top:

```python
async def step(self, ...):
    await self._sync_roles_from_db()
    # ... existing step logic ...
```

If `step()` is currently sync, it may need to become async, OR the sync call can be done at the orchestrator level before calling into the solver.

**Check**: Verify whether `SolveEngine.step()` or its caller is already async. If the orchestrator's step loop is async (likely since it awaits action execution), the sync can happen there:

```python
# In orchestrator's main step loop:
if self._entity_graph:
    await self.solve_engine._sync_roles_from_db()
```

### Step 6: NoOp fallback

When `entity_graph` is None (NoOp mode, no KuzuDB):
- `_sync_roles_from_db()` returns immediately (no-op)
- `_flush_role_writes()` returns immediately (no-op)
- `_set_role()` writes to `_object_roles` only (cache-only, same as current behavior)
- All existing behavior is preserved

## Concrete File Changes

### `agents/arc3/entity_graph.py`
- Add `persist_role(color_id, role, confidence, position, level)` method (~20 lines)
- Add `load_all_roles(level) -> Dict[int, ObjectRole]` method (~30 lines)

### `agents/arc3/solver.py`
- Add `self._entity_graph = None` and `self._pending_role_writes = []` to `SolveEngine.__init__`
- Add `self._current_level = 0` to track level
- Add `_set_role(color_id, role)` helper method (~8 lines)
- Add `_flush_role_writes()` async method (~10 lines)
- Add `_sync_roles_from_db()` async method (~12 lines)
- Replace 5 direct `self._object_roles[color_id] = ...` assignments in `_merge_persistent_roles()` with `self._set_role(color_id, ...)`
- Replace direct assignment in `_demote_extra_primaries()` (line 2604 area) if it writes roles

### `agents/arc3/orchestrator.py`
- `_bootstrap_entity_discovery()`: after writing to `_object_roles`, also persist via `entity_graph.persist_role()` (~5 lines added)
- `merge_graph_roles()`: make async, persist to KuzuDB, simplify (~15 lines)
- Add `_entity_graph` reference (set from runner)
- Add `self._current_level` tracking

### `agents/arc3/runner.py`
- After creating `EntityGraphBuilder`, set `orchestrator.solve_engine._entity_graph = entity_graph`
- Change `orchestrator.merge_graph_roles(...)` to `await orchestrator.merge_graph_roles(...)`
- Add sync call: `await orchestrator.solve_engine._sync_roles_from_db()` in the step loop (or let orchestrator handle it)

### `tests/test_b169_kuzu_role_source.py` (new)
- Test `persist_role` writes to KuzuDB (mock db, verify Cypher)
- Test `load_all_roles` returns correct ObjectRole dict from KuzuDB rows
- Test `_sync_roles_from_db` populates `_object_roles` from DB
- Test `_set_role` writes to both cache and pending writes
- Test `_flush_role_writes` persists all pending writes
- Test NoOp fallback: when `_entity_graph` is None, all methods are no-ops, existing behavior unchanged
- Test merge conflict resolution still works (B148 grounding, demotion)
- Test `seed_bootstrap_roles` results appear in KuzuDB after `_bootstrap_entity_discovery`

## API/Schema/Test Updates

- No schema changes — `GridEntity.inferred_role` and `GridEntity.role_confidence` already exist
- No tool catalog changes
- No adapter allow-list changes
- `merge_graph_roles()` becomes async — update any callers

## Acceptance Criteria

- [ ] `seed_bootstrap_roles()` results are persisted to KuzuDB GridEntity nodes
- [ ] `_merge_persistent_roles()` results are persisted to KuzuDB GridEntity nodes
- [ ] `_sync_roles_from_db()` populates `_object_roles` from KuzuDB at start of each step
- [ ] B168 `merge_graph_roles()` writes to KuzuDB (not directly to `_object_roles`)
- [ ] After sync, `_object_roles` matches KuzuDB state exactly
- [ ] B148 grounding rules (confidence >= 0.7 preservation) still work
- [ ] Demotion of extra primaries still works
- [ ] When KuzuDB unavailable (NoOp mode), falls back to in-memory-only — existing behavior preserved
- [ ] All existing ARC tests pass (`pytest tests/test_arc3_*.py -q`)

## Validation Commands

```bash
python3 -m pytest tests/test_b169_kuzu_role_source.py -q
python3 -m pytest tests/test_arc3_solver.py -q
python3 -m pytest tests/test_arc3_orchestrator.py -q
python3 -m pytest tests/test_b168_graph_exploration.py -q
python3 -m pytest tests/ -q --timeout=60
```

## Risks / Constraints

- **Async boundary**: `_merge_persistent_roles` is sync. The `_set_role` + `_flush_role_writes` pattern avoids mixing async into sync merge logic. The flush happens at the async boundary (end of step). Gemini must follow this pattern — do NOT scatter `await` calls inside `_merge_persistent_roles`.
- **KuzuDB MERGE semantics**: Verify that KuzuDB's `MERGE ... ON CREATE SET ... ON MATCH SET` works as expected (Kuzu v0.11.3). If not, use `MATCH + SET` with a preceding existence check.
- **Level transitions**: When the puzzle advances to a new level, `_sync_roles_from_db()` must query the new level. Roles from previous levels should NOT carry over unless explicitly intended.
- **Performance**: `load_all_roles` adds one KuzuDB query per step. With ~5-10 entities, this should be sub-millisecond. If profiling shows otherwise, add a dirty flag so sync only runs when writes have occurred.

## Done When

- All role writers persist to KuzuDB
- `_object_roles` is populated from KuzuDB each step
- NoOp mode works identically to current behavior
- No regressions in existing tests
