# B-119 - Bootstrap Entity Discovery and SideQuests Persistence

## Metadata

- Card: B119
- Priority: P0
- Dependencies: B95, B117

## Summary

Run initial entity discovery during bootstrap so the agent has entity-level context from step 0.
Currently `ObjectRoleMapper` only runs during the `solve` phase (after hypothesize), meaning the
agent's first several decisions have zero entity awareness.

## Technical Approach

### Key constraint

`ObjectRoleMapper.update()` normally consumes `hypothesis_context` with transition data (what
changed between frames). At bootstrap (step 0), there are no transitions yet. The bootstrap
entity scan must therefore use a **geometry-only heuristic**:

- Run `_compute_centroids()` on the initial grid.
- Use color distribution, spatial extent, and static-row coverage to assign preliminary roles.
- Tag all bootstrap roles with `confidence < 0.5` and `source: "bootstrap_geometry"`.
- These preliminary roles get refined by the normal `ObjectRoleMapper.update()` in subsequent solve
  steps.

### Implementation steps

1. Add `ObjectRoleMapper.bootstrap_scan(observation) -> Dict[int, ObjectRole]` — a lightweight
   geometry-only scan that doesn't need `hypothesis_context`.
2. Add `ARCOrchestrator._bootstrap_entity_discovery(observation)` — calls the scan, stores result
   in `self._entity_map`, and writes to SideQuests via `notify_turn`.
3. Call `_bootstrap_entity_discovery()` inside `perceive()` when `step == 0`, after the structure
   summary ingestion.
4. Merge bootstrap roles into `SolveEngine._object_roles` on first `solve()` call so they seed
   (not override) the evidence-based mapper.

## Concrete File Changes

### `agents/arc3/solver.py`

Add method to `ObjectRoleMapper`:

```python
def bootstrap_scan(self, observation: Dict[str, Any]) -> Dict[int, ObjectRole]:
    """Geometry-only entity scan for bootstrap (no transition data needed).

    Uses grid centroids, color counts, and spatial extent to make preliminary
    role guesses. All roles are tagged with low confidence and 'bootstrap_geometry'
    source so the evidence-based mapper can override them.
    """
    grid = observation.get("grid") or []
    colors = observation.get("colors") or []
    roles: Dict[int, ObjectRole] = {}

    if not grid:
        return roles

    centroids = _compute_centroids(grid)
    total_pixels = sum(int(v.get("count", 0)) for v in centroids.values()) or 1

    for color_info in colors:
        color_id = color_info["value"] if isinstance(color_info, dict) else color_info
        if color_id == self.BACKGROUND_COLOR:
            continue
        centroid = centroids.get(color_id)
        if centroid is None:
            continue

        count_fraction = float(centroid.get("count", 0)) / total_pixels
        has_wall_geometry = self._has_wall_geometry(centroid)

        role = ObjectRole(color_id=color_id, evidence_steps=[0])

        if count_fraction > 0.15 and has_wall_geometry:
            role.role = RoleType.WALL
            role.confidence = 0.40
        elif count_fraction <= 0.03:
            role.role = RoleType.GOAL
            role.confidence = 0.35
        elif count_fraction <= 0.08:
            role.role = RoleType.PLAYER
            role.confidence = 0.30
        else:
            role.role = RoleType.UNKNOWN
            role.confidence = 0.20

        role.estimated_position = {"row": centroid["row"], "col": centroid["col"]}
        roles[color_id] = role

    return roles
```

Add method to `SolveEngine`:

```python
def seed_bootstrap_roles(self, bootstrap_roles: Dict[int, ObjectRole]) -> None:
    """Seed object roles from bootstrap scan. Evidence-based updates will override."""
    for color_id, role in bootstrap_roles.items():
        if color_id not in self._object_roles:
            self._object_roles[color_id] = role
```

### `agents/arc3/orchestrator.py`

Add method:

```python
async def _bootstrap_entity_discovery(self, observation: ARC3Observation) -> Dict[int, Any]:
    """Run preliminary entity scan and persist to SideQuests."""
    bootstrap_roles = self.solve_engine.role_mapper.bootstrap_scan(observation)

    if not bootstrap_roles:
        self._entity_map = {}
        return {}

    # Build entity map summary
    entity_map = {}
    for color_id, role in bootstrap_roles.items():
        entity_map[color_id] = {
            "role": role.role.value,
            "confidence": role.confidence,
            "position": role.estimated_position,
            "source": "bootstrap_geometry",
        }

    self._entity_map = entity_map

    # Persist to SideQuests
    entity_summary = "[ENTITY MAP] Bootstrap entity discovery: " + "; ".join(
        f"color {cid} = {info['role']} (conf={info['confidence']:.2f})"
        for cid, info in entity_map.items()
    )
    notify_response = await self.brain.notify_turn(
        role="assistant", content=entity_summary, session_id=self.session_id
    )
    self._record_write_event(
        kind="entity_discovery",
        summary=entity_summary,
        detail={"entity_map": entity_map, "source": "bootstrap"},
        response_dict=notify_response,
    )

    # Seed solve engine
    self.solve_engine.seed_bootstrap_roles(bootstrap_roles)

    return entity_map
```

Update `__init__` to initialize `self._entity_map = {}`.

Update `perceive()` to call `_bootstrap_entity_discovery()` when `step == 0`:

```python
# After structure_summary notify_turn, before retrieval triggers:
if step == 0:
    await self._bootstrap_entity_discovery(observation)
```

### `agents/arc3/runner.py`

No changes needed — the runner already calls `perceive(observation, step=0)` during bootstrap.
The entity discovery is triggered inside perceive.

### `tests/test_arc3_orchestrator.py`

Add tests:

```python
def test_bootstrap_entity_discovery_multi_color():
    """Entity map is populated after bootstrap perceive on multi-color grid."""

def test_bootstrap_entity_discovery_single_color():
    """Entity map is empty (no crash) on single-color grid."""

def test_bootstrap_entity_map_persisted_to_sidequests():
    """Entity map is written via notify_turn with [ENTITY MAP] tag."""

def test_bootstrap_roles_seed_solve_engine():
    """Bootstrap roles appear in solve_engine._object_roles."""
```

### `tests/test_arc3_durable_runner.py`

Add test:

```python
def test_entity_discovery_in_bootstrap_write_trace():
    """Entity discovery write event appears in bootstrap_write_trace."""
```

## Validation Commands

```bash
pytest -q tests/test_arc3_orchestrator.py -k "bootstrap_entity"
pytest -q tests/test_arc3_durable_runner.py -k "entity_discovery"
pytest -q tests/test_arc3_orchestrator.py tests/test_arc3_durable_runner.py
```

## Risks / Constraints

- Bootstrap scan is geometry-only, so role guesses will be low confidence. This is intentional —
  the evidence-based mapper refines them after real transitions.
- Do NOT change `ObjectRoleMapper.update()` behavior. The bootstrap scan is additive.
- Do NOT make the bootstrap scan an LLM call. It must be algorithmic and fast.
- Seed roles must not override evidence-based roles: `seed_bootstrap_roles` only fills gaps.

## Outcome

The agent has a preliminary entity map from step 0. Even if `available_actions` is constrained,
the agent knows what entities exist on the grid and can reason about them.
