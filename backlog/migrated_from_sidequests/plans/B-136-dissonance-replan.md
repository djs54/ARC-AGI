# B136 Plan: Trigger Re-Planning on Dissonance in Runner Loop

## Summary

`runner.py` calls `orchestrator.plan()` only once at startup. When the solve engine sets `dissonance=True`, the runner ignores it and continues with a stale plan. This plan adds a minimal check after each solve call and triggers re-planning when warranted, with a backoff to prevent thrashing.

## Current Code Pattern (runner.py)

```python
# Startup: plan called once
await orchestrator.plan(initial_observation, memory_context)

# Main step loop (simplified)
for step_num in range(max_steps):
    obs = await adapter.observe()
    solve_result = await orchestrator.solve(obs, step_num)
    action = await orchestrator.act(step_num)
    await adapter.submit(action)
    # <-- NO check of solve_result["dissonance"] here
```

## Technical Approach

### Step 1: Add backoff counter

Add to `DurableARCRunner.__init__`:
```python
self._last_replan_step: int = -999  # step when last re-plan fired
self._replan_backoff_steps: int = 3   # minimum steps between re-plans
```

### Step 2: Add dissonance check after solve

After `await orchestrator.solve(...)` returns, insert:

```python
# Check if solve engine detected plan-reality mismatch
_dissonance = bool((orchestrator._solve_context or {}).get("dissonance"))
_backoff_ok = (step_num - self._last_replan_step) >= self._replan_backoff_steps

if _dissonance and _backoff_ok:
    self._last_replan_step = step_num
    # Re-run plan phase with current observation and memory context
    await orchestrator.plan(current_observation, memory_context)
    # Emit a trace event via orchestrator if accessible
    if hasattr(orchestrator, "_emit_trace_event"):
        orchestrator._emit_trace_event("replan_triggered", {
            "step": step_num,
            "reason": "dissonance detected by solve engine",
            "dissonance_detail": orchestrator._solve_context.get("dissonance_reason", "unknown"),
        })
```

### Step 3: Ensure `memory_context` is available in loop

Verify `memory_context` (the retrieval result from the perceive phase) is kept in scope for the duration of the step loop. If it's a local variable only in the first iteration, assign it to `self._last_memory_context` at perceive time and read it back here.

### Step 4: Do not change `orchestrator.plan()` itself

This card only changes when `plan` is called. Do not modify the plan phase implementation.

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/runner.py` | Add `_last_replan_step`, `_replan_backoff_steps` to `__init__`; add dissonance check + conditional `await orchestrator.plan(...)` after each solve call |

## Validation Commands

```bash
# Run 15-step smoke test (use a puzzle known to produce dissonance)
ARC_API_KEY="$(jq -r '.key' benchmarks/arc3/.arc/arc.json)" \
  .venv/bin/python run_single_puzzle.py --puzzle-id 007bbfb7 --max-steps 15

# Check re-plan events fired
jq '[.[] | select(.event == "replan_triggered")] | length' master_timeline.json
jq '[.[] | select(.event == "replan_triggered")] | .[].data' master_timeline.json

# Check plan phase fired more than once
jq '[.[] | select(.event == "phase_start" and .data.phase == "plan")] | length' master_timeline.json

# Confirm backoff: no more re-plans than ceil(max_steps / backoff_steps)
# For 15 steps with backoff=3: max 5 re-plans (but typically ≤2 if dissonance resolves)

# Run tests
cd sidequests-brain && .venv/bin/pytest -q tests/
```

## Acceptance Criteria (checklist)

- [ ] `replan_triggered` event appears at least once when solve dissonance is True
- [ ] `plan` phase `phase_start` event fires more than once in a run with dissonance
- [ ] Re-plans respect 3-step backoff (no two `replan_triggered` events within 3 steps of each other)
- [ ] `_last_replan_step` and `_replan_backoff_steps` present in `DurableARCRunner.__init__`
- [ ] `pytest -q tests/` passes

## Notes / Risks

- If `orchestrator.plan()` is expensive (multiple LLM calls), the backoff is critical. Start with `_replan_backoff_steps = 3`; tune upward if needed.
- `memory_context` scope: if the loop doesn't have access to `memory_context` after the first iteration, use `self._last_memory_context = memory_context` at perceive time.
- Do not re-run the perceive phase as part of re-planning — only re-run `plan()` with the currently available observation.
- This card's value multiplies greatly once B132 is in (LLM live → re-plan produces real new goals).
