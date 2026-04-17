# Plan for B173 — Persist GameRuleHypothesis via Hypothesis Nodes

## Card Metadata

- **Card ID**: B173
- **Priority**: P1
- **Dependencies**: B170 (hypothesis persistence)

## Summary

`GameRuleHypothesis` objects are stored in TWO in-memory locations: `SolveEngine._game_rule_hypotheses` (solver.py:1766) and `ARCOrchestrator._game_rule_hypothesis` (orchestrator.py). Neither writes to KuzuDB. This plan persists them as `Hypothesis` nodes with `category = "game_rule"` and eliminates the orchestrator's duplicate store.

## Current State

### GameRuleHypothesis dataclass (solver.py:77-87)

```python
@dataclass
class GameRuleHypothesis:
    rule_description: str
    action_semantics: Dict[str, str]    # {"ACTION1": "move up", ...}
    objective_description: str
    level_strategy: str
    confidence: float
    evidence: List[str]
    contradictions: List[str]
    source: str                          # "level_analysis" | "llm" | "memory"
```

### Dual storage

1. `SolveEngine._game_rule_hypotheses: List[GameRuleHypothesis]` (solver.py:1766)
2. `ARCOrchestrator._game_rule_hypothesis: GameRuleHypothesis` (orchestrator.py)

Search for `_game_rule_hypothesis` in orchestrator.py and `_game_rule_hypotheses` in solver.py to find all read/write sites.

### Mapping to Hypothesis node

The existing `Hypothesis` schema has all needed fields:
- `id` → derive from task_id + hash of rule_description
- `description` → rule_description
- `category` → "game_rule"
- `confidence` → confidence
- `game_type` → source
- `task_id` → from context
- `status` → "active" / "confirmed"
- `evidence_count` → len(evidence)
- `text_raw` → JSON of action_semantics + objective + strategy

## Technical Approach

### Step 1: Map GameRuleHypothesis to Hypothesis nodes

Create a helper that converts:

```python
def _game_rule_to_hypothesis_params(self, grh: GameRuleHypothesis, task_id: str) -> dict:
    import hashlib, json
    hid = f"grh_{task_id}_{hashlib.md5(grh.rule_description.encode()).hexdigest()[:8]}"
    return {
        "id": hid,
        "desc": grh.rule_description,
        "cat": "game_rule",
        "conf": grh.confidence,
        "gtype": grh.source,
        "tid": task_id,
        "status": "confirmed" if grh.confidence >= 0.8 else "active",
        "ecount": len(grh.evidence),
        "raw": json.dumps({
            "action_semantics": grh.action_semantics,
            "objective": grh.objective_description,
            "level_strategy": grh.level_strategy,
            "evidence": grh.evidence,
            "contradictions": grh.contradictions,
        }),
    }
```

### Step 2: Persist on creation/update

Same pending-writes pattern. When `_game_rule_hypotheses` is set in solver.py, schedule persistence:

```python
def _set_game_rule_hypotheses(self, hypotheses: List[GameRuleHypothesis]):
    self._game_rule_hypotheses = hypotheses
    self._pending_grh_writes = list(hypotheses)
```

Flush writes to KuzuDB using the same `_persist_hypothesis` method from B170 (since GameRuleHypothesis maps to Hypothesis nodes).

### Step 3: Wire GENERALIZES edges

When a game rule hypothesis applies across levels (same rule_description, different levels), create a GENERALIZES edge:

```python
# After persisting, check for same-description hypotheses on other levels
await self._entity_graph.db.execute_write(
    """
    MATCH (h1:Hypothesis {id: $id1}), (h2:Hypothesis)
    WHERE h2.category = 'game_rule' AND h2.task_id = $tid
      AND h2.id <> $id1 AND h2.description = $desc
    MERGE (h1)-[:GENERALIZES]->(h2)
    """,
    {"id1": hid, "tid": task_id, "desc": grh.rule_description}
)
```

### Step 4: Eliminate orchestrator duplicate

In orchestrator.py:
1. Remove `self._game_rule_hypothesis` instance variable
2. Replace all reads of `self._game_rule_hypothesis` with reads from `self.solve_engine._game_rule_hypotheses[0]` (or a helper method)
3. Remove all writes to `self._game_rule_hypothesis`

Search for `_game_rule_hypothesis` (singular) in orchestrator.py to find all sites. Each one should be replaced with a read from the solver's list.

## Concrete File Changes

### `agents/arc3/solver.py`
- Add `_set_game_rule_hypotheses()` method (~5 lines)
- Add `_game_rule_to_hypothesis_params()` helper (~15 lines)
- Add `_flush_grh_writes()` async method (~15 lines)
- Replace all `self._game_rule_hypotheses = ...` with `self._set_game_rule_hypotheses(...)`
- Add `self._pending_grh_writes = []` to `__init__`

### `agents/arc3/orchestrator.py`
- Remove `self._game_rule_hypothesis` from `__init__`
- Replace all reads with `self.solve_engine._game_rule_hypotheses[0] if self.solve_engine._game_rule_hypotheses else None`
- Remove all direct writes (let solver be the single owner)
- Add a convenience property if needed: `@property def _game_rule_hypothesis(self): ...`

### `tests/test_b173_game_rule_hypothesis_persistence.py` (new)
- Test GameRuleHypothesis maps correctly to Hypothesis node params
- Test persistence writes to KuzuDB
- Test GENERALIZES edge creation across levels
- Test orchestrator reads from solver (no duplicate)
- Test NoOp fallback

## Validation Commands

```bash
python3 -m pytest tests/test_b173_game_rule_hypothesis_persistence.py -q
python3 -m pytest tests/test_arc3_solver.py tests/test_arc3_orchestrator.py -q
```

## Risks / Constraints

- **Orchestrator coupling**: The orchestrator reads `_game_rule_hypothesis` in several places for prompt construction. The convenience property keeps the interface stable while eliminating the duplicate.
- **List vs single**: Solver has a List, orchestrator had a single. Use `[0]` or `None` pattern.
- **B170 dependency**: This card assumes B170 has landed `_persist_hypothesis()`. If not, duplicate the method locally.

## Done When

- GameRuleHypothesis persists as Hypothesis nodes with category "game_rule"
- Orchestrator no longer has its own copy
- GENERALIZES edges link cross-level rules
- No regressions
