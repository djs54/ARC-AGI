# Plan: A-135 — Wire Graph Read Tools Into Planner and Vet

## Context

The graph MCP tools are write-heavy, read-light. Every phase records to the graph, but only `fetch_goal_evidence` reads back. Three powerful read tools sit unused:

| Tool | What it returns | Who should use it |
|------|----------------|-------------------|
| `arc_get_untested_actions` | Actions the graph has never seen attempted for this task | Planner — merge into candidate pool |
| `arc_check_action_gate` | Go/no-go signal from accumulated evidence | Vet — graph-backed veto |
| `arc_get_causal_path` | Causal chain: action → effect → hypothesis support/contradiction | Evaluator — real progress check |
| `arc_get_action_evidence` | Per-action evidence: attempts, effects, supports, contradictions | Planner — per-candidate scoring |

## Implementation

### Phase 1: Planner reads untested actions and action evidence

**File: `agents/arc4/graph_queries.py`**

Add two new methods to `ArcGraphQueryPort`:

```python
def fetch_untested_actions(self) -> list[str]:
    """Return action IDs the graph has never seen attempted."""
    result = self._call_tool("fetch_untested_actions", {"task_id": self.task_id})
    # Normalize to list of action_id strings
    ...

def fetch_action_evidence(self, action_id: str) -> dict[str, Any]:
    """Return accumulated evidence for a specific action."""
    result = self._call_tool("fetch_action_evidence", {
        "task_id": self.task_id, 
        "action_id": action_id,
    })
    # Return {supports: int, contradictions: int, confidence: float, ...}
    ...
```

**File: `agents/arc4/ports.py`**

Extend `GraphQueryPort` protocol:

```python
class GraphQueryPort(Protocol):
    ...
    def fetch_untested_actions(self) -> list[str]: ...
    def fetch_action_evidence(self, action_id: str) -> dict[str, Any]: ...
```

**File: `agents/arc4/plan_generator.py`**

In `_available_actions()`, add graph untested actions as a source:

```python
# Existing sources: observation, perception metadata, goal metadata, graph records
# NEW: direct untested-action query
if graph_port is not None:
    untested = graph_port.fetch_untested_actions()
    for action_id in untested:
        if action_id not in candidates:
            candidates.append(action_id)
```

In `_build_candidates()`, use per-action evidence for scoring:

```python
if graph_port is not None:
    evidence = graph_port.fetch_action_evidence(action_id)
    graph_score = evidence.get("confidence", 0.0)
    # Deduct for contradictions
    contradictions = evidence.get("contradictions", 0)
    if contradictions > 0:
        graph_score -= self._limits.falsification_penalty * contradictions
```

This requires threading `graph_port` through to `_build_candidates`, which currently doesn't receive it.

### Phase 2: Vet reads action gate

**File: `agents/arc4/graph_queries.py`**

Add method:

```python
def check_action_gate(self, action_id: str) -> dict[str, Any]:
    """Graph-backed go/no-go for an action."""
    result = self._call_tool("check_action_gate", {
        "task_id": self.task_id,
        "action_id": action_id,
    })
    # Return {allowed: bool, reason: str, evidence_summary: {...}}
    ...
```

**File: `agents/arc4/plan_vetter.py`**

Add optional `graph_port` parameter to `vet()`. Before the existing veto checks:

```python
if graph_port is not None and candidate is not None:
    gate = graph_port.check_action_gate(candidate.action_id)
    if not gate.get("allowed", True):
        # Graph-backed veto
        decision = VetDecision(
            approved=False,
            candidate=candidate,
            reason=f"graph evidence: {gate.get('reason', 'blocked')}",
            alternative=alternative,
            should_replan=True,
            metadata={"veto_type": "graph_evidence", "gate": gate},
        )
        return PhaseResult(...)
```

This requires the vet phase callable to receive a `graph_port`. Currently the vet protocol is `(state, perception, goal, plan)`. Options:
- A: Inject graph_port into PlanVetter at construction time (cleanest)
- B: Pass graph_port through plan metadata (hacky)
- C: Extend VetPhase protocol (breaks existing callsites)

**Recommend option A**: `PlanVetter.__init__(self, limits, graph_port=None)`.

### Phase 3: Evaluator reads causal paths

**File: `agents/arc4/graph_queries.py`**

Add method:

```python
def fetch_causal_path(self, action_id: str) -> dict[str, Any]:
    """Trace causal chain from action to hypothesis support/contradiction."""
    result = self._call_tool("fetch_causal_path", {
        "task_id": self.task_id,
        "action_id": action_id,
    })
    # Return {path_exists: bool, supports: [...], contradicts: [...], ...}
    ...
```

**File: `agents/arc4/evaluator.py`**

The evaluator already has `graph_query_port` injected. Add causal path check:

```python
# After computing meaningful_progress but before returning
if self._graph_query_port is not None and meaningful_progress:
    causal = self._graph_query_port.fetch_causal_path(execution.action_id)
    if causal.get("path_exists") and not causal.get("supports"):
        # Action has causal chain but only contradictions — no real progress
        meaningful_progress = False
        stale_override = True
```

## Dependency on hippocampy

All MCP tools called here (`arc_get_untested_actions`, `arc_check_action_gate`, `arc_get_causal_path`) must be implemented in the hippocampy repo's `campy/adapters/mcp_server.py`. If they return empty/stub results, the planner falls back to local scoring (existing behavior). The ARC_AGI side should handle empty/error responses gracefully.

Check hippocampy for:
- Are these tools already implemented with real Kuzu queries?
- Do they accept `task_id` and `action_id` params as expected?
- What's the response schema?

## Files to modify

- `agents/arc4/graph_queries.py` — 3 new methods
- `agents/arc4/ports.py` — extend GraphQueryPort protocol
- `agents/arc4/plan_generator.py` — consume untested actions and per-action evidence
- `agents/arc4/plan_vetter.py` — consume action gate
- `agents/arc4/evaluator.py` — consume causal path
- `agents/arc4/__init__.py` — wire graph_port into PlanVetter construction
- `run_single_puzzle.py` — pass graph_port to vet (if needed)

## Risks

- **Latency**: 3 extra MCP calls per cycle. Each is an MCP stdio round-trip (~50-200ms). Budget impact: ~0.5s per cycle, acceptable for a 6-phase loop that already takes ~10s.
- **Stale graph data**: If `record_action_effect` hasn't been called yet (step 0), graph queries return empty. All consumers must handle empty gracefully.
- **hippocampy tool maturity**: These tools may return stub/partial data. Phase 1 should verify what hippocampy actually returns before building scoring logic on top.

## Testing strategy

- Unit tests with mock graph_port returning scripted evidence
- Integration test: 5-step workflow where graph accumulates contradictions → planner diversifies → vet vetoes → evaluator detects no causal support
- `make test-a` green
