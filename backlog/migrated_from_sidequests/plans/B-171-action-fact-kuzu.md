# Plan for B171 — Add ActionFact Node Type and Persist to KuzuDB

## Card Metadata

- **Card ID**: B171
- **Priority**: P1
- **Dependencies**: B170 (hypothesis persistence)

## Summary

`HypothesisManager.action_facts` (Python dict at `hypothesis.py:336`) stores deterministic action facts with no KuzuDB backing. There is no `ActionFact` node type in the schema at all. This plan adds the schema and wires persistence.

## Current State

### ActionFact dataclass (hypothesis.py:93-104)

```python
@dataclass
class ActionFact:
    id: str
    action: str                    # "ACTION1", "ACTION4", etc.
    fact_type: str                 # deterministic_effect | blocked | loop | low_value | no_op
    description: str               # "ACTION4 moves player right by 1 cell"
    consistency: float             # 0.0-1.0
    value_status: str
    evidence_count: int
    trend: Dict[str, Any] | None
    support_steps: List[int]
```

### Storage (hypothesis.py:336)

```python
self.action_facts: Dict[str, ActionFact] = {}
```

### Writers

Search `self.action_facts[` in `hypothesis.py` to find all creation sites. Key method: `_extract_action_facts()` which processes action evidence into facts.

### Related B168 node: ActionEffect (schema.py)

B168 already creates `ActionEffect` nodes during behavioral exploration. ActionFacts are higher-level summaries derived from multiple ActionEffects.

## Technical Approach

### Step 1: Add ActionFact node type to schema.py

```python
# In NODE_TABLES dict
"ActionFact": """
    fact_id          STRING,
    task_id          STRING,
    level            INT32,
    action_id        STRING,
    fact_type        STRING,
    description      STRING,
    consistency      DOUBLE,
    value_status     STRING,
    evidence_count   INT32,
    delta_row        DOUBLE,
    delta_col        DOUBLE,
    created_at       TIMESTAMP,
    last_updated     TIMESTAMP,
    PRIMARY KEY (fact_id)
""",
```

### Step 2: Add relationships to schema.py

```python
# In REL_TABLES list
"CREATE REL TABLE IF NOT EXISTS DERIVED_FROM_FACT (FROM ActionFact TO ActionEffect, step INT32)",
"CREATE REL TABLE IF NOT EXISTS SUPPORTS_HYPOTHESIS (FROM ActionFact TO Hypothesis, weight FLOAT)",
```

### Step 3: Add persistence to HypothesisManager

```python
async def _persist_action_fact(self, fact: ActionFact) -> None:
    if not self.brain or not getattr(self.brain, 'db', None):
        return
    fact_id = f"{self._task_id}_{fact.id}"
    await self.brain.db.execute_write(
        """
        MERGE (f:ActionFact {fact_id: $fid})
        ON CREATE SET f.task_id = $tid, f.level = $level,
                      f.action_id = $action, f.fact_type = $ftype,
                      f.description = $desc, f.consistency = $cons,
                      f.value_status = $vs, f.evidence_count = $ec,
                      f.created_at = timestamp($now)
        ON MATCH SET f.consistency = $cons, f.value_status = $vs,
                     f.evidence_count = $ec, f.last_updated = timestamp($now)
        """,
        {
            "fid": fact_id, "tid": self._task_id, "level": self._current_level,
            "action": fact.action, "ftype": fact.fact_type,
            "desc": fact.description, "cons": fact.consistency,
            "vs": fact.value_status, "ec": fact.evidence_count,
            "now": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
    )
```

### Step 4: Wire DERIVED_FROM_FACT edges

When an ActionFact is derived from B168 ActionEffect observations, link them:

```python
# After persisting fact, link to source ActionEffect if available
await self.brain.db.execute_write(
    """
    MATCH (f:ActionFact {fact_id: $fid}), (e:ActionEffect {effect_id: $eid})
    MERGE (f)-[:DERIVED_FROM_FACT {step: $step}]->(e)
    """,
    {"fid": fact_id, "eid": effect_id, "step": step}
)
```

### Step 5: Wire SUPPORTS_HYPOTHESIS edges

When a fact supports a hypothesis (e.g. "ACTION4 moves right" supports "this is a navigation puzzle"), create the edge:

```python
await self.brain.db.execute_write(
    """
    MATCH (f:ActionFact {fact_id: $fid}), (h:Hypothesis {id: $hid})
    MERGE (f)-[:SUPPORTS_HYPOTHESIS {weight: $w}]->(h)
    """,
    {"fid": fact_id, "hid": hypothesis_id, "w": confidence}
)
```

### Step 6: Same pending-writes + flush pattern as B170

Add `self._pending_fact_writes = []` to init. Append on write. Flush at end of `observe()`.

## Concrete File Changes

### `mcp_engine/schema.py`
- Add `ActionFact` to `NODE_TABLES` dict (~15 lines)
- Add `DERIVED_FROM_FACT` and `SUPPORTS_HYPOTHESIS` to `REL_TABLES` list (2 lines)

### `agents/arc3/hypothesis.py`
- Add `self._pending_fact_writes = []` to `__init__`
- Add `_persist_action_fact()` method (~20 lines)
- At every `self.action_facts[key] = fact` site, append to pending writes
- Flush alongside hypothesis writes at end of `observe()`

### `tests/test_b171_action_fact_persistence.py` (new)
- Test `_persist_action_fact` writes correct Cypher
- Test ActionFact node has all required fields
- Test `DERIVED_FROM_FACT` edge creation
- Test `SUPPORTS_HYPOTHESIS` edge creation
- Test NoOp fallback

## Validation Commands

```bash
python3 -m pytest tests/test_b171_action_fact_persistence.py -q
python3 -m pytest tests/test_arc3_solver.py tests/test_arc3_orchestrator.py -q
```

## Risks / Constraints

- **ActionEffect IDs**: Need to ensure B168 ActionEffect `effect_id` format is stable and queryable for `DERIVED_FROM_FACT` edges
- **Schema migration**: Adding a new node type requires KuzuDB to run the CREATE TABLE on next startup. Existing databases will auto-migrate via the idempotent schema init.

## Done When

- ActionFact node type exists in schema
- Facts persist to KuzuDB on creation/update
- Edges link facts to ActionEffects and Hypotheses
- No regressions
