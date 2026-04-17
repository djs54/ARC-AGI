# Plan for B197 — ARC Agent: Load Procedures Before Solving

## Card Metadata
- **Card ID**: B197
- **Priority**: P1
- **Dependencies**: B194 (procedural memory), B180 (cost tracker)

## Summary
Add `recall_procedures` to BrainClientProtocol. Before each puzzle solve, query for applicable Procedures and use their steps as the initial PlanChunk sequence. Track application via APPLIED_PROCEDURE edges.

## Technical Approach

### Step 1: Add recall_procedures to BrainClientProtocol (adapter.py)

```python
# In BrainClientProtocol (abstract base)
async def recall_procedures(self, archetype: str, limit: int = 3) -> list[dict]:
    """Retrieve proven solve procedures for the given archetype."""
    ...

# In LedgerBrainClient
async def recall_procedures(self, archetype: str, limit: int = 3) -> list[dict]:
    result = await self._call_tool("recall_procedures", {
        "archetype": archetype, "limit": limit
    })
    return result.get("procedures", [])

# In NoOpBrainClient
async def recall_procedures(self, archetype: str, limit: int = 3) -> list[dict]:
    return []
```

### Step 2: Pre-solve lookup in runner.py

In `DurableARCRunner._run_puzzle()`, before creating the orchestrator:

```python
# Attempt to load a proven procedure for this puzzle type
archetype_hint = self._guess_archetype_from_metadata(puzzle_meta)  # optional fast heuristic
procedures = await self.brain.recall_procedures(archetype_hint or "unknown")

# Pass to orchestrator config
orchestrator_config["loaded_procedures"] = procedures
if procedures:
    _logger.info(f"Loaded {len(procedures)} procedures for archetype={archetype_hint}")
```

### Step 3: Procedure-guided mode in orchestrator.py

In `ARCOrchestrator.__init__()`:

```python
self._loaded_procedures = config.get("loaded_procedures", [])
```

In `SolveEngine.solve()`, when creating the initial PlanChunk sequence:

```python
if self._loaded_procedures:
    # Use procedure steps as initial chunk sequence
    procedure = self._loaded_procedures[0]  # highest success_rate
    steps = json.loads(procedure.get("steps_json", "[]"))
    for i, step in enumerate(steps):
        chunk = PlanChunk(
            chunk_id=f"proc_{i}",
            action=step.get("action", "explore"),
            precondition=step.get("precondition", ""),
            expected_outcome=step.get("expected_outcome", ""),
            source="procedure",
        )
        self._plan_chunks.append(chunk)
    self._using_procedure = procedure.get("procedure_id")
else:
    # Normal: generate from scratch
    self._using_procedure = None
```

### Step 4: Track application via APPLIED_PROCEDURE edges

After puzzle completes in runner.py:

```python
if orchestrator._using_procedure:
    success = result.get("correct", False)
    await self.brain.call_tool("report_outcome", {
        "plan_id": orchestrator._current_plan_id,
        "procedure_id": orchestrator._using_procedure,
        "success": success,
    })
```

The `report_outcome` handler (tools/__init__.py) updates procedure stats:
```python
# If procedure_id provided, update application stats
if procedure_id:
    await db.execute_write("""
        MATCH (plan:Plan {plan_id: $pid}), (proc:Procedure {procedure_id: $proc_id})
        MERGE (plan)-[:APPLIED_PROCEDURE {success: $success, applied_at: timestamp($now)}]->(proc)
        SET proc.application_count = proc.application_count + 1,
            proc.last_applied_at = timestamp($now)
    """, {"pid": plan_id, "proc_id": procedure_id, "success": success, "now": now})
    # Update running success_rate
    await db.execute_write("""
        MATCH (proc:Procedure {procedure_id: $proc_id})
        SET proc.success_rate = toFloat(proc.success_count + CASE WHEN $success THEN 1 ELSE 0 END)
            / toFloat(proc.application_count)
    """, {"proc_id": procedure_id, "success": success})
```

### Step 5: Fallback when procedure fails

In SolveEngine, if dissonance is detected while following procedure steps:
```python
if self._using_procedure and self._dissonance_count >= 3:
    _logger.warning(f"Procedure {self._using_procedure} failed — falling back to normal solve")
    self._plan_chunks.clear()
    self._using_procedure = None
    # Continue with normal solve behavior
```

### Step 6: Tests

Create `tests/test_b197_arc_procedure_loading.py`:
1. Test recall_procedures called before orchestrator creation
2. Test procedure steps become initial PlanChunk sequence
3. Test APPLIED_PROCEDURE edge created after puzzle completes
4. Test success_rate and application_count updated correctly
5. Test fallback when dissonance detected during procedure execution
6. Test NoOpBrainClient returns empty (no crash)
7. Test no procedure available → normal solve behavior

## Verification
```bash
pytest tests/test_b197_arc_procedure_loading.py -v
```
