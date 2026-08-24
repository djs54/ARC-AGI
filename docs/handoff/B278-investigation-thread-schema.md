# Handoff: B278 New tools needed — investigation-thread durability (Reasoner state machine)

**For:** hippocampy / Campy owner (B278 owns brain internals; ARC consumes across the MCP seam)
**From:** ARC_AGI trajectory-reasoner design (docs/superpowers/specs/2026-08-23-trajectory-reasoner-design.md)
**Status:** ARC-side client (this handoff's companion card, A201) ships degrading cleanly to capability_missing; new server-side schema and tools needed for the Reasoner's decisions to actually persist

## Summary

ARC_AGI is building a "Reasoner" -- a component that owns the advance/repeat/terminate decision each cycle (previously scattered across independent gates with no single owner). Its decisions must be durable and queryable, not held only in a Python process's memory -- specifically so a crashed/restarted process can resume an in-progress investigation by querying the graph, and so "what is currently being investigated" is graph-resident state other queries (like invalid_action_rate-style checks) can actually see.

## Ask: four new tools

### 1. `arc_start_or_resume_thread` (read/create)

Request:
```json
{"task_id": "...", "anchor_ref": "...", "anchor_type": "goal" | "entity"}
```

Behavior: if an `InvestigationThread` already exists for this `(task_id, anchor_ref)` in a non-terminal state, return it (a resume). Otherwise create a new one in state `"exploring"`.

Response:
```json
{"thread_id": "...", "state": "exploring", "resumed": false, "last_cycle": null}
```

or, when resuming:
```json
{"thread_id": "...", "state": "deepening", "resumed": true, "last_cycle": {"cycle_id": "...", "step": 4, "action_sent": true, "action_confirmed_by_observation": false}}
```

### 2. `arc_write_thread_state` (write)

Request:
```json
{"thread_id": "...", "state": "deepening"}
```

Updates `InvestigationThread.state` and `state_updated_at`. This is the durable decision write -- called every time the Reasoner resolves a new state.

### 3. `arc_write_cycle` (write, write-ahead)

Request:
```json
{"thread_id": "...", "step": 4, "action_sent": true}
```

Creates a new `Cycle` node (`action_confirmed_by_observation` defaults false), linked `(:InvestigationThread)-[:HAS_CYCLE]->(:Cycle)` and chained from the previous cycle via `(:Cycle)-[:NEXT]->(:Cycle)`. Called **before** the real ARC API action is sent -- write intent first, always (see spec section 7).

Response:
```json
{"cycle_id": "..."}
```

### 4. `arc_confirm_cycle` (write)

Request:
```json
{"cycle_id": "...", "decision": "repeat_deepen", "confirmed": true}
```

Sets `action_confirmed_by_observation` and records the resolved decision on the cycle, after the real API call returns (or after resume-time reconciliation against a real observation determines what actually happened).

## Suggested schema

```
(:Attempt {task_id, game_id, started_at})
(:InvestigationThread {thread_id, task_id, anchor_ref, anchor_type, state, state_updated_at})
(:Attempt)-[:HAS_THREAD]->(:InvestigationThread)
(:InvestigationThread)-[:ANCHORED_ON]->(:GridEntity | :Hypothesis)
(:InvestigationThread)-[:HAS_CYCLE]->(:Cycle {step, decision, action_sent, action_confirmed_by_observation, started_at, completed_at})
(:Cycle)-[:NEXT]->(:Cycle)
```

Two constraints from ARC's own review, worth preserving in your implementation:

1. `InvestigationThread.state` must be a **directly indexed** property (primary-key-style lookup by `(task_id, anchor_ref)`) -- resume must be an O(1) lookup, not a traversal of the Cycle chain.
2. `Cycle` nodes must hang off `InvestigationThread` (per-attempt, bounded), **not** attached directly to the persistent `GridEntity`/`Hypothesis`/`Rule` nodes the aggregate cross-game memory layer (B309-era work) depends on for fast queries -- otherwise a frequently-revisited entity's fan-out grows unboundedly over the system's lifetime and turns it into a supernode.

## ARC-side status (no action needed from you on this half)

- `agents/arc4/graph_queries.py` gains `start_or_resume_thread`, `write_thread_state`, `write_cycle`, `confirm_cycle`, each calling the tool names above via the existing `_call_tool` helper, each degrading to a defined empty/no-op result on `capability_missing` -- confirmed absent currently, degrades cleanly, not an error.
- These are not yet wired into any live decision path -- that's separate, dependent cards (A202-A205) still to come.

## How ARC will know it's fixed

Once these tools exist server-side, ARC's A204 card (resume/crash-safety logic) will call `arc_start_or_resume_thread` at process startup for a live episode and confirm it stops returning `capability_missing`, then verify a full write/resume round trip against a real crash-injection test.
