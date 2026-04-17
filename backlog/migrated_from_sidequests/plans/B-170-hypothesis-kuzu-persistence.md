# Plan for B170 — Persist Hypotheses to KuzuDB

## Card Metadata

- **Card ID**: B170
- **Priority**: P0
- **Dependencies**: None (Hypothesis schema already exists)

## Summary

The `Hypothesis` node type is fully defined in `schema.py:372-386` with 6 relationship types, but zero code writes to it. `HypothesisManager.hypotheses` (a Python dict at `hypothesis.py:335`) is the sole store. This plan wires hypothesis lifecycle events to KuzuDB.

## Current State

### Hypothesis schema (already exists, no changes needed)

```
Hypothesis node (schema.py:372-386):
  id, description, category, confidence, game_type, task_id,
  status, evidence_count, text_raw, embedding, created_at
```

Relationships (schema.py:531-535, 548):
- `HYPOTHESIZED_IN` (FROM Hypothesis TO Session)
- `CONFIRMS` (FROM Concept TO Hypothesis, weight)
- `CONTRADICTS` (FROM Concept TO Hypothesis, weight)
- `GENERALIZES` (FROM Hypothesis TO Hypothesis)
- `PRODUCED_HYPOTHESIS` (FROM Plan TO Hypothesis)
- `ENTITY_HYPOTHESIS` (FROM GridEntity TO Hypothesis, weight, step)

### Hypothesis dataclass (hypothesis.py:56-91)

```python
@dataclass
class Hypothesis:
    id: str
    description: str
    category: str           # "game_rule", "action_effect", "entity_role", etc.
    confidence: float
    status: str             # "active", "confirmed", "pruned"
    support_count: int
    contradiction_count: int
    evidence: List[str]
    source: str
    created_at: str
```

### HypothesisManager (hypothesis.py:320-338)

```python
class HypothesisManager:
    def __init__(self, brain_client, session_id):
        self.brain = brain_client
        self.session_id = session_id
        self.hypotheses: Dict[str, Hypothesis] = {}   # <-- THE SHADOW STORE
        self.action_facts: Dict[str, ActionFact] = {}  # <-- B171 scope
```

### Writers to self.hypotheses (hypothesis.py)

Search for `self.hypotheses[` to find all creation/update sites. Key methods:
- `_generate_hypotheses()` — creates new Hypothesis objects
- `Hypothesis.update(supported)` — updates confidence, support/contradiction counts, status

### KuzuDB access pattern

`HypothesisManager.__init__` receives `brain_client` which has a `.db` property (the `KuzuClient`). Same pattern as `EntityGraphBuilder` in B168.

## Technical Approach

### Step 1: Add persistence methods to HypothesisManager

```python
async def _persist_hypothesis(self, hyp: Hypothesis) -> None:
    """Write hypothesis to KuzuDB. MERGE to handle create-or-update."""
    if not self.brain or not getattr(self.brain, 'db', None):
        return
    await self.brain.db.execute_write(
        """
        MERGE (h:Hypothesis {id: $id})
        ON CREATE SET h.description = $desc, h.category = $cat,
                      h.confidence = $conf, h.game_type = $gtype,
                      h.task_id = $tid, h.status = $status,
                      h.evidence_count = $ecount, h.text_raw = $raw,
                      h.created_at = timestamp($now)
        ON MATCH SET h.confidence = $conf, h.status = $status,
                     h.evidence_count = $ecount
        """,
        {
            "id": hyp.id, "desc": hyp.description, "cat": hyp.category,
            "conf": hyp.confidence, "gtype": getattr(hyp, 'game_type', ''),
            "tid": getattr(self, '_task_id', ''), "status": hyp.status,
            "ecount": hyp.support_count + hyp.contradiction_count,
            "raw": hyp.description,
            "now": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
    )

async def _persist_hypothesis_session_link(self, hyp_id: str) -> None:
    """Create HYPOTHESIZED_IN edge to current session."""
    if not self.brain or not getattr(self.brain, 'db', None):
        return
    await self.brain.db.execute_write(
        """
        MATCH (h:Hypothesis {id: $hid}), (s:Session {session_id: $sid})
        MERGE (h)-[:HYPOTHESIZED_IN]->(s)
        """,
        {"hid": hyp_id, "sid": self.session_id}
    )

async def load_hypotheses(self, task_id: str) -> Dict[str, Hypothesis]:
    """Load hypotheses from KuzuDB for a given task. Returns dict keyed by id."""
    if not self.brain or not getattr(self.brain, 'db', None):
        return {}
    rows = await self.brain.db.execute_read(
        """
        MATCH (h:Hypothesis)
        WHERE h.task_id = $tid AND h.status <> 'pruned'
        RETURN h.id AS id, h.description AS description,
               h.category AS category, h.confidence AS confidence,
               h.status AS status, h.evidence_count AS evidence_count
        """,
        {"tid": task_id}
    )
    result = {}
    for row in rows:
        result[row["id"]] = Hypothesis(
            id=row["id"], description=row["description"],
            category=row.get("category", ""),
            confidence=row.get("confidence", 0.5),
            status=row.get("status", "active"),
            support_count=row.get("evidence_count", 0),
            contradiction_count=0,
            evidence=[], source="kuzu",
            created_at="",
        )
    return result
```

### Step 2: Wire persistence into hypothesis lifecycle

Find every site where `self.hypotheses[key] = hypothesis` is written. After each write, schedule persistence:

```python
# Pattern: after creating/updating a hypothesis
self.hypotheses[hyp.id] = hyp
self._pending_hypothesis_writes.append(hyp)
```

Add a flush method called at the end of `observe()`:

```python
async def _flush_hypothesis_writes(self):
    for hyp in self._pending_hypothesis_writes:
        await self._persist_hypothesis(hyp)
    self._pending_hypothesis_writes.clear()
```

**Important**: `observe()` is currently sync. Either:
- Make it async (preferred — the runner's step loop is already async)
- Or use the same `_pending_writes` + `_flush()` pattern from B169

Check whether `observe()` callers are async. If so, make `observe()` async and add `await self._flush_hypothesis_writes()` at the end.

### Step 3: Wire ENTITY_HYPOTHESIS edges

In `agents/arc3/entity_graph.py`, when `run_inference()` generates role inferences from entity observations, create `ENTITY_HYPOTHESIS` edges:

```python
# When an entity observation supports a hypothesis
await self.db.execute_write(
    """
    MATCH (e:GridEntity {entity_id: $eid}), (h:Hypothesis {id: $hid})
    MERGE (e)-[:ENTITY_HYPOTHESIS {weight: $w, step: $step}]->(h)
    """,
    {"eid": entity_id, "hid": hypothesis_id, "w": weight, "step": step}
)
```

This connects the B168 entity graph to the hypothesis system.

### Step 4: Load hypotheses at start of solve

In the runner, after creating `HypothesisManager`, load existing hypotheses:

```python
# In runner.py, after HypothesisManager creation
existing = await hypothesis_manager.load_hypotheses(task.task_id)
hypothesis_manager.hypotheses.update(existing)
```

### Step 5: NoOp fallback

All persistence methods check `if not self.brain or not getattr(self.brain, 'db', None)` and return early. When running with `NoOpBrainClient`, all behavior is identical to current.

## Concrete File Changes

### `agents/arc3/hypothesis.py`
- Add `self._pending_hypothesis_writes = []` to `__init__`
- Add `self._task_id` to `__init__` (passed from runner)
- Add `_persist_hypothesis()` method (~25 lines)
- Add `_persist_hypothesis_session_link()` method (~10 lines)
- Add `load_hypotheses()` method (~25 lines)
- Add `_flush_hypothesis_writes()` method (~5 lines)
- At every `self.hypotheses[key] = hyp` site, add `self._pending_hypothesis_writes.append(hyp)`
- At end of `observe()`, call flush (or make `observe` async and await it)

### `agents/arc3/entity_graph.py`
- In `run_inference()`, after entity-based role inference, create `ENTITY_HYPOTHESIS` edges (~10 lines)
- Requires hypothesis IDs — either passed in or derived from a naming convention

### `agents/arc3/runner.py`
- Pass `task_id` to `HypothesisManager.__init__`
- After creation, call `await hypothesis_manager.load_hypotheses(task.task_id)`

### `tests/test_b170_hypothesis_persistence.py` (new)
- Test `_persist_hypothesis` writes correct Cypher (mock db)
- Test `load_hypotheses` returns correct Hypothesis objects from rows
- Test `_flush_hypothesis_writes` clears pending list
- Test NoOp fallback: no db calls when brain.db is None
- Test `HYPOTHESIZED_IN` edge creation
- Test hypothesis update (confidence change) persists via MERGE

## Acceptance Criteria

- [ ] Creating a hypothesis writes a Hypothesis node to KuzuDB
- [ ] Updating hypothesis confidence updates the node
- [ ] Confirming sets `status = "confirmed"`, refuting sets `status = "pruned"`
- [ ] `HYPOTHESIZED_IN` edge links hypothesis to session
- [ ] `ENTITY_HYPOTHESIS` edges link GridEntity to Hypothesis
- [ ] `load_hypotheses()` returns hypotheses from KuzuDB
- [ ] NoOp fallback preserves existing behavior
- [ ] All existing ARC tests pass

## Validation Commands

```bash
python3 -m pytest tests/test_b170_hypothesis_persistence.py -q
python3 -m pytest tests/test_arc3_orchestrator.py tests/test_arc3_solver.py -q
python3 -m pytest tests/ -q --timeout=60
```

## Risks / Constraints

- **Async boundary**: `observe()` may be sync. Check callers before making it async. Use pending-writes pattern if callers can't be changed.
- **Hypothesis ID stability**: Ensure hypothesis IDs are deterministic (e.g. hash of description + category) so MERGE works correctly across reloads.
- **Embedding column**: The Hypothesis schema has an `embedding FLOAT[384]` column. Populating it is optional for this card — leave as NULL for now. A future card can add semantic search over hypotheses.

## Done When

- Zero ghost relationships — every Hypothesis rel type has at least one writer
- Hypotheses survive a HypothesisManager restart (load from KuzuDB)
- No regressions
