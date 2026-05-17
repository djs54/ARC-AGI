# Plan: A-064 — memory firewall for execute hot path

## Card metadata

- **Card:** A064
- **Priority:** P0
- **Layer:** transport/client seam
- **Depends on:** A059, A061

## Summary

Prevent memory calls from blocking action execution. The smoke timed out through the MCP daemon after heavy memory traffic while executing repeated `ACTION6`. This card defines phase-aware memory policy: memory can guide modeling and replanning, but execute/macro phases must be fast and degrade gracefully.

Graph-solution classification: this is graph workload governance and performance. SideQuest graph memory is useful for provenance and recall, but execute-phase graph reads/writes are the wrong workload shape. Apply “filter early, expand late” and “no unbounded traversals in production” as runtime policy.

## Implementation approach

1. Define memory phase policy:
   - `perceive/hypothesize/model/route/replan`: reads allowed with configured timeouts and cache
   - `execute/macro`: blocking reads disallowed
   - `evaluate`: writes allowed only as deferred/coalesced summaries
2. Add a guard at the MCP client/adapter boundary:
   - if current phase disallows blocking memory, return cached result or `{"status": "skipped", "reason": "execute_memory_firewall"}`
   - schedule writes for deferred flush where available
3. Coalesce memory writes:
   - repeated action-effect writes during macro mode become one summary write
   - include counts, action id, observed deltas, and stop reason
4. Fix failure classification:
   - daemon HTTP timeout should map to `tool_timeout` or `memory_timeout`
   - wall-clock exhaustion should map to `wall_clock_budget_exhausted`
   - avoid generic `llm_timeout` unless an actual LLM call timed out
5. Add trace visibility:
   - `memory_firewall_action=skipped|cached|deferred|flushed`
   - `memory_firewall_reason`
   - `memory_degraded`
6. Add graph-memory budget controls:
   - per-phase read/write allowance
   - per-run graph write coalescing
   - bounded path/hop metadata in provenance summaries
   - no execute-phase fan-out reads

## Concrete file additions/edits

- `sidequest_mcp_client/mcp_brain_client.py`
  - Add optional phase-aware call policy and nonblocking/deferred behavior.
- `benchmarks/arc3/adapter.py`
  - Preserve firewall metadata in ledger entries.
- `agents/arc3/orchestrator.py`
  - Set/propagate current phase and macro state to memory policy.
- `agents/arc3/runner.py`
  - Flush deferred memory summaries at safe boundaries.
- `agents/arc3/failure_taxonomy.py`
  - Ensure memory/tool timeout classification precedence.
- `tests/test_a064_memory_firewall.py`
  - New fixtures for execute skip, macro defer, flush, and timeout classification.

## API/interface changes

- No external MCP protocol changes required.
- Add optional internal fields/config:
  - `memory_policy.current_phase`
  - `memory_policy.execute_reads=skip|cache_only`
  - `memory_policy.execute_writes=defer`
  - `memory_firewall_action`
  - `memory_firewall_reason`

## Graph-memory policy notes

Allowed during `hypothesize/model/route/replan`:

- bounded recall by task/archetype/action/scene hash
- cached graph evidence reuse
- compact provenance query with explicit limit

Disallowed during `execute/macro`:

- graph similarity fan-out
- broad lesson recall
- per-step action-effect writes
- unbounded path traversal

Deferred write shape should summarize a macro or action batch as one provenance record with stable ids and bounded evidence.

## Tests to add or run

Add tests for:

- execute phase skips blocking `recall_lessons`
- macro phase defers repeated `upsert_lesson`
- deferred write flush emits one summary
- MCP daemon timeout classified as memory/tool timeout
- LLM timeout classification still works for real LLM timeout

Validation commands:

```bash
pytest -q tests/test_a064_memory_firewall.py tests/test_mcp_brain_client.py tests/test_b185_failure_taxonomy.py
pytest -q tests/test_a061_single_action_macro_executor.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- A skipped memory call in execute is not an error if cached strategy context exists.
- If no cached memory exists, execute should still proceed when action policy is already validated.
- Deferred memory writes should be best effort and must not change the executed ARC trajectory.
