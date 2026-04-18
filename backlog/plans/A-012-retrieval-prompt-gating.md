# A-012 - Retrieval Is Expensive and Invisible to the Prompt

## Card metadata

- Card: A012
- Priority: P0
- Depends on: A008, A011

## Summary

Make every ARC-side retrieval call justify itself against the next prompt. Gate, dedupe, and rekey retrievals so the cost on the MCP seam maps to actual prompt content, and fix the two downstream data surfaces (plan adherence evaluator, `notify_turn` offline queue) that currently obscure what happened.

## Implementation approach

1. Retrieval gating: in `agents/arc3/orchestrator.py`, wrap each retrieval call site with a precondition that asks "will this retrieval be rendered in the upcoming prompt block?" Use the block-trace shape already present in `prompt_trace[i].block_trace` to know which blocks the next prompt will produce.
2. Dedup: store the last query fingerprint per retrieval call kind in the orchestrator (`{call: (query_hash, ts)}`). If the same fingerprint would fire inside `N` steps, short-circuit and annotate a `retrieval_dedup` trace event. Default N=1 for `current_truth` and `recall_relevant_lessons`; N=∞ (disabled) for `register_plan` (idempotency handled in A011) and `report_outcome`.
3. Rekey `recall_procedures`: replace the `game_id` input with a structural key `{archetype, victory_condition_type, sorted(available_actions)}` JSON-serialized. Expose the key in trace metadata.
4. Client-level short-circuit: add an opt-in `in_memory_cache_for=["current_truth"]` in `sidequest_mcp_client/mcp_brain_client.py` with TTL 5s and maxsize 32 entries. Emit `mcp_cache_hit` observability metric when a cached response is served.
5. Plan-adherence fix: in `benchmarks/arc3/trajectory_eval.py`, read registered plans from the same source the orchestrator writes them to (likely `solve_ctx.active_chunk.plan_id` or `sidequests_ledger` register_plan entries). Add a unit test that registers a plan, runs evaluation, and asserts a non-`no active chunk plans recorded` outcome.
6. `notify_turn queued_offline` investigation: add a diagnostic log in `sidequest_mcp_client/mcp_brain_client.py` (or wherever the "queued_offline" result_summary is constructed) that captures why queueing failed (timeout vs. seam not ready vs. backpressure). If root cause is on the SideQuests side, open a follow-on card and document the dependency. If it is ARC-side (e.g., a race between MCP ready signal and first `notify_turn`), fix here.
7. Tests:
   - `tests/test_retrieval_gating.py` — gating/dedup/rekey
   - `tests/test_trajectory_eval_plan_adherence.py` — plan adherence data wiring
   - `tests/test_mcp_cache_hit.py` — cache-hit short-circuit

## Concrete file additions/edits

- edit `agents/arc3/orchestrator.py`
- edit `agents/arc3/runner.py`
- edit `sidequest_mcp_client/mcp_brain_client.py`
- edit `benchmarks/arc3/trajectory_eval.py`
- add `tests/test_retrieval_gating.py`
- add `tests/test_trajectory_eval_plan_adherence.py`
- add `tests/test_mcp_cache_hit.py`
- update `ARCHITECTURE.md` Runtime Notes section to describe the client cache and retrieval gating

## API/interface changes

- `MCPBrainClient.call_tool` gains an optional cache lookup path (transparent to callers)
- `trajectory_eval.plan_adherence` return shape gains a concrete ratio when data is available
- no changes to the MCP JSON-RPC wire contract

## Tests to add or run

- `pytest -q tests/test_retrieval_gating.py`
- `pytest -q tests/test_trajectory_eval_plan_adherence.py`
- `pytest -q tests/test_mcp_cache_hit.py`
- re-run the one-puzzle smoke and compare `submission_results_arcServer.json` retrieval counts before/after

## Validation commands

- `pytest -q -k "retrieval or plan_adherence or mcp_cache"`
- `python run_single_puzzle.py --smoke` (or the existing smoke command) and diff retrieval counts

## Assumptions/defaults

- SideQuests procedures are keyed in a way compatible with an `(archetype, victory_condition_type, available_actions)` tuple; if not, this card delegates the alignment to a SideQuests follow-on card
- the in-memory client cache is invalidated on each new puzzle (fresh orchestrator instance) — it is per-run, not persistent
- fixing `queued_offline` on the ARC side is in scope only if the cause is ARC-side; otherwise the card produces a concrete SideQuests dependency
