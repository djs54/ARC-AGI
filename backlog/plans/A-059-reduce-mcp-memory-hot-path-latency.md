# Plan: A-059 — reduce MCP memory hot-path latency

## Card metadata

- **Card:** A059
- **Priority:** P1
- **Layer:** transport/client seam
- **Depends on:** A051, A058

## Summary

After A051, `notify_turn` is effectively non-blocking, but live smoke still shows repeated 5-12s delays in other MCP memory calls. Add hot-path budgets, caching/deduplication, relevance gating, and safe deferral for calls that do not need to block the next action.

Graph-solution classification: this is an optimization and testing card for a traversal-first memory workload. Keep SideQuest as the graph owner, but make ARC's MCP calls use graph-shaped keys and bounded traversal semantics.

## Implementation approach

1. Define per-tool hot-path policies in one place, preferably `sidequest_mcp_client/mcp_brain_client.py`:
   - synchronous budget
   - cache key
   - TTL/run-window behavior
   - fallback result shape
   - trace label
2. Add short-run caches for repeated empty or stable responses:
   - `recall_procedures("race") -> 0 results`
   - identical `register_plan` fingerprints
   - repeated archetype/action-effect `recall_lessons` queries within a small window
3. Gate expensive reads in `agents/arc3/orchestrator.py` and/or `agents/arc3/runner.py` so they run only when:
   - exploration phase changes
   - archetype/victory hypothesis changes
   - action coverage crosses a threshold
   - A058 terminal-grounded value is ambiguous
   - a refresh interval has elapsed
4. Defer per-step `upsert_lesson` writes after local trace durability. If deferral already exists for `notify_turn`, follow the same style but keep result accounting explicit.
5. Add ledger entries for `cache_hit`, `dedup_hit`, `deferred`, `skipped_by_gate`, and `timed_out_fallback`.
6. Keep all behavior behind the MCP client seam; do not import memory internals.
7. Replace broad text-only query keys with structured graph keys where the caller has them:
   - `scene_wl_hash`
   - `frame_hash`
   - `scene_node_count`
   - `archetype`
   - `victory_condition`
   - `action_id`
   - `outcome_class`
8. Add dense-hub guardrails for generic recall. Queries that only specify `race`, `reach_goal`, or a common action id should be downgraded, skipped, or combined with a more selective key.

## Hot graph queries and cache policy

Action prior query:

```text
key = (scene_wl_hash, archetype, victory_condition)
SceneState -> SIMILAR_TO*0..1 -> SceneState -> TOOK -> Action -> PRODUCED -> ActionEffect -> HAD_OUTCOME -> Outcome
filter terminal_value_score > threshold or levels_completed_delta > 0
limit 5
```

Negative evidence query:

```text
key = (scene_wl_hash, action_id)
SceneState -> SIMILAR_TO*0..1 -> SceneState -> TOOK(Action) -> PRODUCED -> ActionEffect -> HAD_OUTCOME -> Outcome
filter no_op_or_loop or levels_completed_delta == 0 across repeated tries
limit 5
```

Plan reuse query:

```text
key = (scene_wl_hash, victory_condition)
SceneState -> SUPPORTED_BY/RECOMMENDED paths through PlanChunk and ActionEffect
filter prior terminal outcome or high terminal_value_score
limit 3
```

Cache defaults:

- Cache empty `recall_procedures` by `(archetype, victory_condition, scene_wl_hash?)`.
- Cache action-effect recalls by `(scene_wl_hash, action_id, outcome_class)`.
- Dedup plan registration by stable plan fingerprint plus scene/victory keys.
- Use short run-scoped TTLs; invalidate on archetype change, victory-condition change, or large scene-WL change.

## Concrete file additions/edits

- `sidequest_mcp_client/mcp_brain_client.py`
  - Add policy helpers, graph-shaped cache keys, dedup keys, dense-hub guards, and fallback labels.
- `sidequest_mcp_client/mcp_session.py`
  - Support call-level budget/fallback metadata if needed.
- `agents/arc3/orchestrator.py`
  - Add action-relevance gates for read calls and pass structured graph facets to MCP wrappers.
- `agents/arc3/runner.py`
  - Ensure deferred writes are locally durable before the step advances.
- `benchmarks/arc3/adapter.py`
  - Preserve ledger reporting for cached/deferred/skipped calls.
- `tests/test_a059_memory_hot_path_latency.py`
  - New focused tests for cache/dedup/defer/gate behavior.
- Existing tests:
  - `tests/test_mcp_brain_client.py`
  - `tests/test_readiness.py`

## API/interface changes

- No public API change required.
- Internal MCP client responses may gain optional metadata:
  - `source: fresh|cache|dedup|deferred|skipped|fallback`
  - `fallback_reason`
  - `latency_budget_ms`
  - `cache_key`
  - `graph_key`
  - `hop_bound`
  - `dense_hub_guard`

## Tests to add or run

Add tests for:

- repeated empty `recall_procedures` returns a cache hit without a second MCP call
- identical plan registration dedups before MCP
- per-step `upsert_lesson` can defer while preserving local trace event
- memory reads are skipped when the action-relevance gate says they cannot affect the next decision
- timeout fallback returns the expected neutral result and trace label
- repeated broad hub-only queries are blocked or combined with selective graph keys
- graph-shaped cache keys dedup calls that equivalent free-form text would miss

Validation commands:

```bash
pytest -q tests/test_a059_memory_hot_path_latency.py tests/test_mcp_brain_client.py tests/test_readiness.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Default cache windows should be conservative: enough to avoid repeated calls in the same local loop, not enough to hide genuine phase changes.
- Do not add new MCP tools for this card unless existing SideQuest wrappers cannot express the graph query. If that happens, write the sibling `sidequests-brain` requirement and keep ARC fallback neutral.
- A cached or skipped result must be distinguishable from a fresh positive memory result in traces and prompts.
- If a memory call was the only source of an action recommendation, A058 terminal-grounded scoring must still be able to reject it.
