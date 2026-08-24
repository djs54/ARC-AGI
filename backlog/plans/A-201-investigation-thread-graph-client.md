# Plan: A201 — Hippocampy Handoff Doc + Client-Side Graph Stubs for Investigation Threads

## Card metadata

- ID: A201
- Priority: P1
- Layer: transport/client seam
- Dependencies: None

## Summary

Write the cross-repo handoff doc asking hippocampy for the `InvestigationThread`/`Cycle` schema and four new MCP tools (spec §6), and add ARC-side client methods to `graph_queries.py` that consume them, degrading cleanly until the server tools exist — same rollout discipline as every prior B278-dependent card this session (A177, A179, A186, A192).

## Technical approach

### 1. Read the reference template first

Read `docs/handoff/B278-entity-neighborhood-query.md` in full before writing the new doc — match its structure and tone exactly (Summary, Ask with request/response JSON shapes, Suggested schema, ARC-side status, How ARC will know it's fixed).

### 2. `docs/handoff/B278-investigation-thread-schema.md` (new)

```markdown
# Handoff: B278 New tools needed — investigation-thread durability (Reasoner state machine)

**For:** hippocampy / Campy owner (B278 owns brain internals; ARC consumes across the MCP seam)
**From:** ARC_AGI trajectory-reasoner design (docs/superpowers/specs/2026-08-23-trajectory-reasoner-design.md)
**Status:** ARC-side client (this handoff's companion card, A201) ships degrading cleanly to capability_missing; new server-side schema and tools needed for the Reasoner's decisions to actually persist

## Summary

ARC_AGI is building a "Reasoner" -- a component that owns the advance/repeat/terminate
decision each cycle (previously scattered across independent gates with no single
owner). Its decisions must be durable and queryable, not held only in a Python
process's memory -- specifically so a crashed/restarted process can resume an
in-progress investigation by querying the graph, and so "what is currently being
investigated" is graph-resident state other queries (like invalid_action_rate-style
checks) can actually see.

## Ask: four new tools

### 1. `arc_start_or_resume_thread` (read/create)

Request: `{"task_id": "...", "anchor_ref": "...", "anchor_type": "goal" | "entity"}`

Behavior: if an `InvestigationThread` already exists for this `(task_id, anchor_ref)`
in a non-terminal state, return it (a resume). Otherwise create a new one in state
`"exploring"`.

Response: `{"thread_id": "...", "state": "exploring", "resumed": false, "last_cycle": null}`
or, when resuming: `{"thread_id": "...", "state": "deepening", "resumed": true, "last_cycle": {"cycle_id": "...", "step": 4, "action_sent": true, "action_confirmed_by_observation": false}}`

### 2. `arc_write_thread_state` (write)

Request: `{"thread_id": "...", "state": "deepening"}`

Updates `InvestigationThread.state` and `state_updated_at`. This is the durable
decision write -- called every time the Reasoner resolves a new state.

### 3. `arc_write_cycle` (write, write-ahead)

Request: `{"thread_id": "...", "step": 4, "action_sent": true}`

Creates a new `Cycle` node (`action_confirmed_by_observation` defaults false),
linked `(:InvestigationThread)-[:HAS_CYCLE]->(:Cycle)` and chained from the
previous cycle via `(:Cycle)-[:NEXT]->(:Cycle)`. Called **before** the real
ARC API action is sent -- write intent first, always (see spec section 7).

Response: `{"cycle_id": "..."}`

### 4. `arc_confirm_cycle` (write)

Request: `{"cycle_id": "...", "decision": "repeat_deepen", "confirmed": true}`

Sets `action_confirmed_by_observation` and records the resolved decision on
the cycle, after the real API call returns (or after resume-time reconciliation
against a real observation determines what actually happened).

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

1. `InvestigationThread.state` must be a **directly indexed** property
   (primary-key-style lookup by `(task_id, anchor_ref)`) -- resume must be an
   O(1) lookup, not a traversal of the Cycle chain.
2. `Cycle` nodes must hang off `InvestigationThread` (per-attempt, bounded),
   **not** attached directly to the persistent `GridEntity`/`Hypothesis`/`Rule`
   nodes the aggregate cross-game memory layer (B309-era work) depends on for
   fast queries -- otherwise a frequently-revisited entity's fan-out grows
   unboundedly over the system's lifetime and turns it into a supernode.

## ARC-side status (no action needed from you on this half)

- `agents/arc4/graph_queries.py` gains `start_or_resume_thread`, `write_thread_state`,
  `write_cycle`, `confirm_cycle`, each calling the tool names above via the
  existing `_call_tool` helper, each degrading to a defined empty/no-op result
  on `capability_missing` -- confirmed absent currently, degrades cleanly, not
  an error.
- These are not yet wired into any live decision path -- that's separate,
  dependent cards (A202-A205) still to come.

## How ARC will know it's fixed

Once these tools exist server-side, ARC's A204 card (resume/crash-safety logic)
will call `arc_start_or_resume_thread` at process startup for a live episode
and confirm it stops returning `capability_missing`, then verify a full
write/resume round trip against a real crash-injection test.
```

### 3. `agents/arc4/graph_queries.py` — four new client methods

Read the file's current structure in full first (it's been modified by several cards this session — confirm exact current line numbers and the existing tool-name-mapping dict's location before editing, don't assume from this plan alone). Add to `ARC_V2_TOOL_NAMES`:

```python
"start_or_resume_thread": "arc_start_or_resume_thread",
"write_thread_state": "arc_write_thread_state",
"write_cycle": "arc_write_cycle",
"confirm_cycle": "arc_confirm_cycle",
```

Add four methods to `ArcGraphQueryPort`, following the exact degrade-on-`capability_missing` pattern already used by `fetch_entity_neighborhood`:

```python
def start_or_resume_thread(self, anchor_ref: Any, anchor_type: str) -> dict[str, Any]:
    """Investigation-thread lookup/create -- resume support for the
    trajectory Reasoner (see docs/superpowers/specs/2026-08-23-trajectory-
    reasoner-design.md). Degrades to a fresh-thread-shaped result when the
    server capability doesn't exist yet."""
    result = self._call_tool(
        "start_or_resume_thread",
        {"task_id": self.task_id, "anchor_ref": anchor_ref, "anchor_type": anchor_type},
    )
    if not isinstance(result, Mapping) or result.get("status") == "capability_missing":
        return {"thread_id": None, "state": "exploring", "resumed": False, "last_cycle": None}
    return {
        "thread_id": result.get("thread_id"),
        "state": str(result.get("state", "exploring")),
        "resumed": bool(result.get("resumed", False)),
        "last_cycle": result.get("last_cycle"),
    }

def write_thread_state(self, thread_id: Any, state: str) -> dict[str, Any]:
    """Durable write of the Reasoner's resolved state. No-op (not an error)
    when thread_id is None -- callers pass None when start_or_resume_thread
    itself degraded, and this must not raise in that case."""
    if thread_id is None:
        return {"status": "skipped", "reason": "no_thread_id"}
    result = self._call_tool("write_thread_state", {"thread_id": thread_id, "state": state})
    return self._normalize_write_result(result, tool_key="write_thread_state")

def write_cycle(self, thread_id: Any, step: int, action_sent: bool) -> dict[str, Any]:
    """Write-ahead call -- must be invoked BEFORE the real API action is
    sent, per spec section 7's write-intent-first invariant. No-op when
    thread_id is None."""
    if thread_id is None:
        return {"cycle_id": None}
    result = self._call_tool("write_cycle", {"thread_id": thread_id, "step": step, "action_sent": action_sent})
    if not isinstance(result, Mapping) or result.get("status") == "capability_missing":
        return {"cycle_id": None}
    return {"cycle_id": result.get("cycle_id")}

def confirm_cycle(self, cycle_id: Any, decision: str, confirmed: bool) -> dict[str, Any]:
    """Post-action (or resume-time reconciliation) confirmation write.
    No-op when cycle_id is None."""
    if cycle_id is None:
        return {"status": "skipped", "reason": "no_cycle_id"}
    result = self._call_tool("confirm_cycle", {"cycle_id": cycle_id, "decision": decision, "confirmed": confirmed})
    return self._normalize_write_result(result, tool_key="confirm_cycle")
```

Confirm `_normalize_write_result` exists with this exact signature (used elsewhere in the file, e.g. by `record_vet`) before assuming it — read its current definition first.

### 4. `ports.py` — explicitly do not add these to the Protocol

Read `agents/arc4/ports.py::GraphQueryPort` in full. Confirm `fetch_entity_history`, `fetch_rules_for_action`, and `fetch_entity_neighborhood` are still absent from it (they were as of A192, but re-verify, don't assume). If confirmed, make no changes to `ports.py` in this card -- the four new methods follow the same `getattr(graph_port, "...", None)` convention at their eventual call sites (A202+), not a Protocol declaration.

## Concrete file changes

| File | Change |
|------|--------|
| `docs/handoff/B278-investigation-thread-schema.md` (new) | Cross-repo ask, per template above |
| `agents/arc4/graph_queries.py` | Four new methods + tool-name-map entries |
| `tests/test_a201_investigation_thread_graph_client.py` (new) | Coverage, see Tests |

## Tests

`tests/test_a201_investigation_thread_graph_client.py`:

1. `start_or_resume_thread` on a mock port returning `{"status": "capability_missing"}` yields `{"thread_id": None, "state": "exploring", "resumed": False, "last_cycle": None}`, not an exception.
2. `start_or_resume_thread` on a mock port returning a real resume payload (`resumed: true`, a `last_cycle` dict) parses it through correctly.
3. `write_thread_state` with `thread_id=None` returns a skipped-status dict without calling `_call_tool` at all (assert the mock's call count is 0).
4. `write_thread_state` with a real `thread_id` calls `_call_tool` with the exact expected payload shape.
5. `write_cycle` with `thread_id=None` returns `{"cycle_id": None}` without calling `_call_tool`.
6. `write_cycle` on a mock port returning `capability_missing` also yields `{"cycle_id": None}`.
7. `write_cycle` on a mock port returning a real `cycle_id` parses it through.
8. `confirm_cycle` with `cycle_id=None` returns a skipped-status dict without calling `_call_tool`.
9. `confirm_cycle` with a real `cycle_id` calls `_call_tool` with the exact expected payload shape.

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a201_investigation_thread_graph_client.py -v
make test-a
make test-all
```

## Assumptions/defaults

- This card ships correct and inert even before hippocampy's schema exists, per the established rollout discipline (A177, A179, A186, A192 all shipped client-side first).
- The `thread_id=None`/`cycle_id=None` no-op guards are deliberate and important: A202-A205 will call these methods unconditionally each cycle, and must not need their own `capability_missing` handling at every call site -- this card's job is to make "the capability doesn't exist yet" fully transparent to every future caller.
