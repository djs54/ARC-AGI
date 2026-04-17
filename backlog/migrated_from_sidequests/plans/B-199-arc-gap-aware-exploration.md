# Plan for B199 — ARC Agent: Knowledge Gap-Aware Exploration Budget

## Card Metadata
- **Card ID**: B199
- **Priority**: P2
- **Dependencies**: B193 (metacognition gaps)

## Summary
Query `get_knowledge_gaps` before each puzzle solve. If a KnowledgeGap exists for the puzzle's archetype, double the Phase 2b exploration budget. Otherwise use default budget.

## Technical Approach

### Step 1: Add get_knowledge_gaps to BrainClientProtocol (adapter.py)

```python
# In BrainClientProtocol (abstract base)
async def get_knowledge_gaps(self, domain: str = None) -> list[dict]:
    """Retrieve active knowledge gaps, optionally filtered by domain."""
    ...

# In LedgerBrainClient
async def get_knowledge_gaps(self, domain: str = None) -> list[dict]:
    params = {}
    if domain:
        params["domain"] = domain
    result = await self._call_tool("get_knowledge_gaps", params)
    return result.get("gaps", [])

# In NoOpBrainClient
async def get_knowledge_gaps(self, domain: str = None) -> list[dict]:
    return []
```

### Step 2: Pre-solve gap check in runner.py

In `DurableARCRunner._run_puzzle()`, after archetype hint and procedure lookup:

```python
# Check for knowledge gaps in this archetype
gaps = await self.brain.get_knowledge_gaps(domain=archetype_hint)
has_gap = any(g.get("gap_type") == "missing_lessons" for g in gaps)

exploration_multiplier = 2.0 if has_gap else 1.0
orchestrator_config["exploration_budget_multiplier"] = exploration_multiplier

if has_gap:
    _logger.info(f"Knowledge gap detected for {archetype_hint} — doubling exploration budget")
```

### Step 3: Configurable exploration budget in orchestrator.py

In `ARCOrchestrator.__init__()`:

```python
self._exploration_budget_multiplier = config.get("exploration_budget_multiplier", 1.0)
```

Where Phase 2b (LLM-guided exploration) budget is set:

```python
# Default: 4 LLM-guided exploration steps
base_exploration_budget = 4
llm_exploration_budget = int(base_exploration_budget * self._exploration_budget_multiplier)
```

### Step 4: Trace logging

```python
if self._exploration_budget_multiplier != 1.0:
    self._record_trace_event("GAP_AWARE_BUDGET", {
        "multiplier": self._exploration_budget_multiplier,
        "base_budget": base_exploration_budget,
        "adjusted_budget": llm_exploration_budget,
        "archetype": archetype_hint,
    })
```

### Step 5: Tests

Create `tests/test_b199_gap_aware_exploration.py`:
1. Test exploration budget doubled when KnowledgeGap exists for archetype
2. Test default budget (1.0x) when no gap exists
3. Test NoOpBrainClient returns empty gaps → default budget
4. Test trace event logged when budget adjusted
5. Test only "missing_lessons" gap type triggers doubling (not other types)
6. Test multiplier passed through config to orchestrator correctly

## Verification
```bash
pytest tests/test_b199_gap_aware_exploration.py -v
```
