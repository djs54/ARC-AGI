# Plan: A-075 — aggregate mechanic memory

## Card metadata

- **Card:** A075
- **Priority:** P0
- **Layer:** transport/client seam
- **Depends on:** A073, A074

## Summary

Add an MCP-seam aggregate memory interface for reusable ARC mechanics. The point is to remember world-model structure across games, not raw episode chatter.

Graph-solution classification: graph is appropriate because retrieval is based on multi-hop relationships among mechanics, action patterns, effect patterns, preconditions, failures, and recoveries. Use labeled property graph semantics. Keep runtime database-independent and access memory only through the MCP client.

## Implementation approach

1. Define mechanic summary payloads in ARC runtime:
   - mechanic id/signature
   - action pattern signature
   - effect pattern signature
   - preconditions
   - coordinate relevance
   - terminal relevance
   - object roles
   - failure modes
   - recovery policies
   - supporting/contradicting evidence counts
   - confidence and source task ids
2. Add `MCPBrainClient` methods:
   - `publish_mechanic_summary(summary, *, async_dispatch=True)`
   - `recall_mechanic_priors(signature, *, limit=5, min_confidence=0.0)`
3. Keep methods tolerant:
   - if the server lacks the tool, return `memory_degraded`/`capability_missing` telemetry without crashing
   - do not block execute phase
4. Update `benchmarks/arc3/adapter.py` ledger wrapper with identical signatures.
5. Update orchestrator/solver:
   - publish compact mechanic summary at game end or reasoning boundary
   - retrieve mechanic priors during solve/model/replan phases only
6. Retrieval should use bounded graph signatures:
   - action-set shape
   - effect classes
   - object-role changes
   - terminal trend class
   - coordinate relevance
   - failure signal
7. Avoid raw logs:
   - publish aggregate counts and evidence snippets only
   - never store full prompt/response bodies as the mechanic object

## Concrete file additions/edits

- `sidequest_mcp_client/mcp_brain_client.py`
  - Add seam methods and soft-failure handling.
- `benchmarks/arc3/adapter.py`
  - Add ledger wrapper methods with compatibility for old mocks.
- `agents/arc3/world_model.py`
  - Add `to_mechanic_summary()`.
- `agents/arc3/orchestrator.py`
  - Publish summaries and retrieve priors at safe boundaries.
- `agents/arc3/solver.py`
  - Consume ranked mechanic priors.
- `tests/test_a075_aggregate_mechanic_memory.py`
  - New tests for method signatures, fallback, and bounded retrieval behavior.

## API/interface changes

New MCP-client methods:

```python
async def recall_mechanic_priors(
    self,
    signature: dict,
    *,
    limit: int = 5,
    min_confidence: float = 0.0,
) -> dict: ...

async def publish_mechanic_summary(
    self,
    summary: dict,
    *,
    async_dispatch: bool = True,
) -> dict: ...
```

If sidequests-brain uses different names, adapt only inside the MCP client; do not import its internals.

## Starter aggregate schema

```text
(:Mechanic {id, name, signature, confidence})
(:ActionPattern {signature})
(:EffectPattern {signature})
(:Precondition {kind, signature})
(:FailureMode {name})
(:RecoveryPolicy {name})
(:PlanTemplate {name})
(:GameArchetype {name})

(:Mechanic)-[:HAS_ACTION_PATTERN]->(:ActionPattern)
(:Mechanic)-[:CAUSES_EFFECT_PATTERN]->(:EffectPattern)
(:Mechanic)-[:REQUIRES]->(:Precondition)
(:Mechanic)-[:FAILS_AS]->(:FailureMode)
(:FailureMode)-[:RECOVERED_BY]->(:RecoveryPolicy)
(:Mechanic)-[:USES_PLAN]->(:PlanTemplate)
(:Mechanic)-[:APPEARS_IN]->(:GameArchetype)
```

## Starter retrieval

```text
Given current per-game signature:
1. Match action-set shape and effect-pattern class.
2. Filter by terminal relevance and coordinate relevance.
3. Expand one hop to recovery policies and plan templates.
4. Return at most 5 ranked mechanics with evidence counts.
```

Bound all traversals by task/session/mechanic signature; avoid expanding from global `ACTION6` or color-only hubs.

## Tests to add or run

Add tests for:

- MCP client signatures and missing-capability fallback
- ledger wrapper preserves arguments
- retrieval is skipped/cache-only during execute phase
- mechanic summaries are compact and omit raw logs
- solver receives ranked priors with bounded size

Validation commands:

```bash
pytest -q tests/test_a075_aggregate_mechanic_memory.py
pytest -q tests/test_mcp_brain_client.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Sidequests-brain may need its own follow-up card if tools are missing.
- ARC runtime should treat aggregate mechanic memory as helpful evidence, never as unquestioned truth.
