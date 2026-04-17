# Plan for B174 — Persist ChunkLedgerEntry to KuzuDB

## Card Metadata

- **Card ID**: B174
- **Priority**: P2
- **Dependencies**: None (Plan/PlanStep nodes already exist with active writers)

## Summary

`SolveEngine._chunk_ledger` (`solver.py:1757`) stores chunk execution history in a Python list. After solve completes, this data is lost. This plan adds a `ChunkExecution` node type and persists entries when chunks complete.

## Current State

### ChunkLedgerEntry dataclass (solver.py:103-109)

```python
@dataclass
class ChunkLedgerEntry:
    description: str
    status: str          # "pending" | "active" | "completed" | "failed"
    steps_used: int
    outcome_summary: str
```

### Storage (solver.py:1757)

```python
self._chunk_ledger: List[ChunkLedgerEntry] = []
```

### Writers

Search `self._chunk_ledger.append(` in solver.py to find all append sites. Key locations:
- After chunk graduation (status = "completed")
- After chunk dissonance/replacement (status = "failed")
- After level end (status depends on outcome)

### Related Plan nodes

`Plan` and `PlanStep` nodes already exist in KuzuDB with writers in `mcp_engine/tools/__init__.py`. `PlanChunk.plan_id` (solver.py:100) already references a SideQuests plan_id.

## Technical Approach

### Step 1: Add ChunkExecution node type to schema.py

```python
"ChunkExecution": """
    execution_id     STRING,
    task_id          STRING,
    level            INT32,
    plan_id          STRING,
    chunk_family     STRING,
    description      STRING,
    status           STRING,
    steps_used       INT32,
    graduation_score DOUBLE,
    evidence_at_end  DOUBLE,
    dissonance_triggered BOOLEAN,
    outcome_summary  STRING,
    created_at       TIMESTAMP,
    PRIMARY KEY (execution_id)
""",
```

### Step 2: Add relationship

```python
"CREATE REL TABLE IF NOT EXISTS EXECUTED_AS (FROM Plan TO ChunkExecution, seq INT32)",
```

### Step 3: Persist on chunk completion

In solver.py, wherever `self._chunk_ledger.append(entry)` is called, also schedule persistence:

```python
def _record_chunk_completion(self, entry: ChunkLedgerEntry, chunk: PlanChunk):
    """Record chunk completion to ledger and schedule KuzuDB persistence."""
    self._chunk_ledger.append(entry)
    if self._entity_graph:
        self._pending_chunk_writes.append((entry, chunk))

async def _flush_chunk_writes(self):
    if not self._entity_graph or not self._pending_chunk_writes:
        return
    for entry, chunk in self._pending_chunk_writes:
        exec_id = f"{self._task_id}_L{self._current_level}_chunk_{len(self._chunk_ledger)}"
        await self._entity_graph.db.execute_write(
            """
            CREATE (c:ChunkExecution {
                execution_id: $eid, task_id: $tid, level: $level,
                plan_id: $pid, chunk_family: $family,
                description: $desc, status: $status,
                steps_used: $steps, graduation_score: $grad,
                evidence_at_end: $evidence,
                dissonance_triggered: $diss,
                outcome_summary: $outcome,
                created_at: timestamp($now)
            })
            """,
            {
                "eid": exec_id, "tid": self._task_id,
                "level": self._current_level,
                "pid": chunk.plan_id or "",
                "family": chunk.source,
                "desc": entry.description, "status": entry.status,
                "steps": entry.steps_used,
                "grad": chunk.graduation_score,
                "evidence": chunk.graduation_components.get("evidence", 0.0),
                "diss": entry.status == "failed",
                "outcome": entry.outcome_summary,
                "now": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        )
        # Link to Plan if plan_id exists
        if chunk.plan_id:
            await self._entity_graph.db.execute_write(
                """
                MATCH (p:Plan {plan_id: $pid}), (c:ChunkExecution {execution_id: $eid})
                MERGE (p)-[:EXECUTED_AS {seq: $seq}]->(c)
                """,
                {"pid": chunk.plan_id, "eid": exec_id, "seq": len(self._chunk_ledger)}
            )
    self._pending_chunk_writes.clear()
```

### Step 4: Replace direct appends

Search for all `self._chunk_ledger.append(` in solver.py. Replace each with `self._record_chunk_completion(entry, self._active_chunk)`.

## Concrete File Changes

### `mcp_engine/schema.py`
- Add `ChunkExecution` to `NODE_TABLES` (~15 lines)
- Add `EXECUTED_AS` to `REL_TABLES` (1 line)

### `agents/arc3/solver.py`
- Add `self._pending_chunk_writes = []` to `__init__`
- Add `_record_chunk_completion()` method (~5 lines)
- Add `_flush_chunk_writes()` async method (~35 lines)
- Replace `self._chunk_ledger.append(...)` calls with `self._record_chunk_completion(...)`

### `tests/test_b174_chunk_ledger_persistence.py` (new)
- Test `_record_chunk_completion` appends to ledger and pending writes
- Test `_flush_chunk_writes` generates correct Cypher
- Test EXECUTED_AS edge links to Plan node
- Test NoOp fallback (no entity_graph → ledger-only)

## Validation Commands

```bash
python3 -m pytest tests/test_b174_chunk_ledger_persistence.py -q
python3 -m pytest tests/test_arc3_solver.py -q
```

## Risks / Constraints

- **Chunk reference**: `_record_chunk_completion` needs the active `PlanChunk` for graduation_score and plan_id. Ensure the chunk reference is available at all append sites.
- **Execution ID uniqueness**: Using `{task_id}_L{level}_chunk_{index}` — ensure index is stable (use ledger length at time of append).

## Done When

- ChunkExecution node type in schema
- Chunk completions persist to KuzuDB
- EXECUTED_AS edges link to parent Plan
- No regressions
