# B138 Plan: Expose SolveEngine Internal Brain Calls in Agent Trace

## Summary

`SolveEngine.solve()` makes brain calls (`recall_plans`, `recall_lessons`, `register_plan`) with no visibility in `agent_trace`. This plan injects a lightweight trace callback into `SolveEngine` so its internal brain I/O appears in the agent_trace stream.

## Current Architecture

```
orchestrator._emit_trace_event  ← writes to orchestrator._execution_trace list
solve_engine.solve()            ← makes brain calls with NO reference to _emit_trace_event
                                  ← these only appear in arc_server stream
```

## Technical Approach

### Step 1: Modify `SolveEngine.__init__` to accept callback

```python
# agents/arc3/solver.py
from typing import Callable, Any

class SolveEngine:
    def __init__(
        self,
        brain,
        # ... existing params ...
        emit_trace_event: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        # ... existing init ...
        self._emit_trace = emit_trace_event  # None = no-op
```

### Step 2: Add a `_trace` helper method

```python
def _trace(self, event: str, data: dict) -> None:
    """Emit a trace event if callback is registered."""
    if self._emit_trace is not None:
        self._emit_trace(event, data)
```

### Step 3: Instrument brain calls in `solve()`

Locate each brain call in `SolveEngine.solve()` and wrap with trace events:

**recall_plans**:
```python
self._trace("solve_recall_plans_start", {"step": step_num})
_t0 = time.perf_counter()
plans = await self.brain.recall_plans(query=self._current_goal)
self._trace("solve_recall_plans_end", {
    "step": step_num,
    "elapsed_ms": round((time.perf_counter() - _t0) * 1000, 1),
    "results": len(plans) if plans else 0,
})
```

**recall_lessons**:
```python
self._trace("solve_recall_lessons_start", {"step": step_num})
_t0 = time.perf_counter()
lessons = await self.brain.recall_lessons(query=self._current_goal)
self._trace("solve_recall_lessons_end", {
    "step": step_num,
    "elapsed_ms": round((time.perf_counter() - _t0) * 1000, 1),
    "results": len(lessons) if lessons else 0,
})
```

**register_plan** (after B137's idempotency guard — only when actually registering):
```python
self._trace("solve_register_plan", {
    "step": step_num,
    "goal": goal,
    "steps": steps,
})
```

### Step 4: Pass callback from orchestrator

In `agents/arc3/orchestrator.py`, where `SolveEngine` is instantiated:

```python
# Find the SolveEngine construction site
self._solve_engine = SolveEngine(
    brain=self._brain,
    # ... existing params ...
    emit_trace_event=self._emit_trace_event,  # <-- ADD THIS
)
```

If `SolveEngine` is constructed once in `__init__`, this is a one-line change.

If `SolveEngine` is transient (created per solve call), pass it there instead.

### Step 5: Verify `_emit_trace_event` signature matches

Check that `orchestrator._emit_trace_event(event: str, data: dict)` matches what `SolveEngine._trace` expects. Both should be `(str, dict) -> None`. If orchestrator's method is async, use `asyncio.create_task` or make `_trace` await the callback — but check the existing signature first.

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/solver.py` | Add `emit_trace_event` param to `__init__`; add `_trace()` helper; wrap `recall_plans`, `recall_lessons`, `register_plan` calls with trace events |
| `agents/arc3/orchestrator.py` | Pass `emit_trace_event=self._emit_trace_event` when constructing `SolveEngine` |

## Validation Commands

```bash
# Run smoke test
ARC_API_KEY="$(jq -r '.key' benchmarks/arc3/.arc/arc.json)" \
  .venv/bin/python run_single_puzzle.py --puzzle-id 007bbfb7 --max-steps 5

# Confirm solve brain calls now appear in agent_trace source
jq '[.[] | select(.source == "agent_trace" and (.event | startswith("solve_recall")))] | length' master_timeline.json

# Confirm elapsed_ms in solve phase is now realistic (>10ms instead of 1ms)
jq '[.[] | select(.event == "solve_phase_end")] | .[].data.elapsed_ms' master_timeline.json

# Confirm SolveEngine works without callback (callback=None path)
# (integration test: instantiate SolveEngine without emit_trace_event and call solve())

# Run tests
cd sidequests-brain && .venv/bin/pytest -q tests/
```

## Acceptance Criteria (checklist)

- [ ] `solve_recall_plans_end`, `solve_recall_lessons_end`, `solve_register_plan` events present in `master_timeline.json` under `source: "agent_trace"`
- [ ] `solve_phase_end.elapsed_ms` reflects actual solve duration (>50ms when brain calls are included)
- [ ] `SolveEngine` instantiates without error when `emit_trace_event=None` (backward compatible)
- [ ] `orchestrator.py` passes `self._emit_trace_event` to SolveEngine at construction
- [ ] No new imports of orchestrator types inside `solver.py` (callback is a plain Callable)
- [ ] `pytest -q tests/` passes

## Notes / Risks

- **No circular imports**: `solver.py` must not import from `orchestrator.py`. The callback is injected as a plain `Callable` — this is safe.
- If `orchestrator._emit_trace_event` is defined as an async method, the callback signature changes. Prefer sync emit (append to list) with async flush — check existing implementation.
- Keep instrumentation points minimal: only the three brain calls identified. Do not instrument internal solver computations (those belong in a future profiling card).
- Coordinate with B137: if B137 changes when `register_plan` is called, make sure B138's trace fires at the same call site (after B137's guard passes).
