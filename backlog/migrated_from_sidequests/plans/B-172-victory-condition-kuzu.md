# Plan for B172 — Persist VictoryCondition to KuzuDB

## Card Metadata

- **Card ID**: B172
- **Priority**: P1
- **Dependencies**: B170 (hypothesis persistence — victory conditions relate to hypotheses)

## Summary

`SolveEngine._victory_condition` (`solver.py:1754`) stores the inferred victory condition as a Python dataclass with no KuzuDB persistence. This plan adds a `VictoryCondition` node type and wires persistence.

## Current State

### VictoryCondition dataclass (solver.py:67-74)

```python
@dataclass
class VictoryCondition:
    condition_type: VictoryType = VictoryType.UNKNOWN
    description: str = ""
    target_color_id: Optional[int] = None
    confidence: float = 0.0
    evidence_steps: List[int] = field(default_factory=list)
    source: str = "unknown"  # "recall_plans" | "llm" | "lesson"
```

### VictoryType enum (solver.py, near line 30)

Search for `class VictoryType` — likely values: UNKNOWN, REACH_GOAL, COLLECT_ALL, ELIMINATE, SURVIVE, TRANSFORM.

### Storage (solver.py:1754)

```python
self._victory_condition: Optional[VictoryCondition] = None
```

### Writers

Search `self._victory_condition =` in solver.py to find all assignment sites. Key locations:
- After LLM inference of victory type
- After recall from plan memory
- After lesson-based inference

## Technical Approach

### Step 1: Add VictoryCondition node type to schema.py

```python
"VictoryCondition": """
    condition_id     STRING,
    task_id          STRING,
    level            INT32,
    condition_type   STRING,
    description      STRING,
    target_color_id  INT32,
    confidence       DOUBLE,
    source           STRING,
    evidence_steps   STRING,
    created_at       TIMESTAMP,
    last_updated     TIMESTAMP,
    PRIMARY KEY (condition_id)
""",
```

### Step 2: Add relationships

```python
"CREATE REL TABLE IF NOT EXISTS INFERRED_FROM (FROM VictoryCondition TO Hypothesis, weight FLOAT)",
"CREATE REL TABLE IF NOT EXISTS REQUIRES_ENTITY (FROM VictoryCondition TO GridEntity, requirement STRING)",
```

### Step 3: Add persistence to SolveEngine

Same `_set` + `_flush` pattern as B169:

```python
def _set_victory_condition(self, vc: VictoryCondition):
    """Set victory condition and schedule KuzuDB persistence."""
    self._victory_condition = vc
    self._pending_vc_write = vc

async def _flush_victory_condition(self):
    if not self._entity_graph or not self._pending_vc_write:
        return
    vc = self._pending_vc_write
    cid = f"{self._task_id}_L{self._current_level}_vc"
    await self._entity_graph.db.execute_write(
        """
        MERGE (v:VictoryCondition {condition_id: $cid})
        ON CREATE SET v.task_id = $tid, v.level = $level,
                      v.condition_type = $ctype, v.description = $desc,
                      v.target_color_id = $tcid, v.confidence = $conf,
                      v.source = $src, v.evidence_steps = $steps,
                      v.created_at = timestamp($now)
        ON MATCH SET v.condition_type = $ctype, v.description = $desc,
                     v.confidence = $conf, v.source = $src,
                     v.evidence_steps = $steps, v.last_updated = timestamp($now)
        """,
        {
            "cid": cid, "tid": self._task_id, "level": self._current_level,
            "ctype": vc.condition_type.value, "desc": vc.description,
            "tcid": vc.target_color_id or -1, "conf": vc.confidence,
            "src": vc.source,
            "steps": ",".join(str(s) for s in vc.evidence_steps),
            "now": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
    )
    self._pending_vc_write = None
```

### Step 4: Replace direct assignments

Search for `self._victory_condition =` in solver.py. Replace each with `self._set_victory_condition(vc)`. Leave `self._victory_condition` reads unchanged (it's the cache).

### Step 5: Wire REQUIRES_ENTITY edges

When victory condition involves specific entities (e.g. "visit intermediate color 5"), create edges:

```python
if vc.target_color_id is not None:
    entity_id = f"{self._task_id}_L{self._current_level}_c{vc.target_color_id}"
    await self._entity_graph.db.execute_write(
        """
        MATCH (v:VictoryCondition {condition_id: $cid}), (e:GridEntity {entity_id: $eid})
        MERGE (v)-[:REQUIRES_ENTITY {requirement: $req}]->(e)
        """,
        {"cid": cid, "eid": entity_id, "req": "target"}
    )
```

### Step 6: Load at start of solve

```python
async def _load_victory_condition(self):
    if not self._entity_graph:
        return
    rows = await self._entity_graph.db.execute_read(
        """
        MATCH (v:VictoryCondition)
        WHERE v.task_id = $tid AND v.level = $level
        RETURN v.condition_type, v.description, v.target_color_id,
               v.confidence, v.source, v.evidence_steps
        ORDER BY v.confidence DESC LIMIT 1
        """,
        {"tid": self._task_id, "level": self._current_level}
    )
    if rows:
        # Reconstruct VictoryCondition from row
        ...
```

## Concrete File Changes

### `mcp_engine/schema.py`
- Add `VictoryCondition` to `NODE_TABLES` (~15 lines)
- Add `INFERRED_FROM` and `REQUIRES_ENTITY` to `REL_TABLES` (2 lines)

### `agents/arc3/solver.py`
- Add `self._pending_vc_write = None` to `SolveEngine.__init__`
- Add `_set_victory_condition()` method (~5 lines)
- Add `_flush_victory_condition()` async method (~25 lines)
- Add `_load_victory_condition()` async method (~20 lines)
- Replace all `self._victory_condition = ...` with `self._set_victory_condition(...)`

### `tests/test_b172_victory_condition_persistence.py` (new)
- Test persistence writes correct Cypher
- Test load returns VictoryCondition from KuzuDB
- Test REQUIRES_ENTITY edge creation
- Test NoOp fallback

## Validation Commands

```bash
python3 -m pytest tests/test_b172_victory_condition_persistence.py -q
python3 -m pytest tests/test_arc3_solver.py -q
```

## Risks / Constraints

- **VictoryType enum**: Ensure `.value` serialization to string works. Verify all VictoryType values are strings.
- **evidence_steps serialization**: Stored as comma-separated string since KuzuDB doesn't have native list-of-int. Parse on load.

## Done When

- VictoryCondition node type in schema
- All victory condition assignments persist to KuzuDB
- REQUIRES_ENTITY edges link to target entities
- No regressions
