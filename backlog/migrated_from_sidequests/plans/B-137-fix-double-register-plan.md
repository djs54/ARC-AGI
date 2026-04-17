# B137 Plan: Fix Double `register_plan` Per Solve Step — Idempotency Guard

## Summary

`SolveEngine.solve()` calls `register_plan` twice per step (top-level plan + chunk plan) and repeats identical registrations across consecutive steps. This plan adds idempotency tracking so `register_plan` is only called when the plan actually changes.

## Current Behavior

In `agents/arc3/solver.py`, `SolveEngine.solve()` contains approximately:

```python
# Called every solve step
await self.brain.register_plan(goal=self._current_goal, steps=self._plan_steps)
# ... chunk planning
await self.brain.register_plan(goal=self._chunk_goal, steps=self._chunk_steps)
```

Both calls fire unconditionally on every step, regardless of whether the plan changed.

## Technical Approach

### Step 1: Add idempotency state to `SolveEngine.__init__`

```python
self._last_registered_top_plan: dict | None = None   # {"goal": ..., "steps": ..., "plan_id": ...}
self._last_registered_chunk_plan: dict | None = None
```

### Step 2: Create a helper method

```python
def _plan_changed(self, last: dict | None, goal: str, steps: int, force: bool = False) -> bool:
    if force:
        return True
    if last is None:
        return True
    return last["goal"] != goal or last["steps"] != steps
```

### Step 3: Wrap each `register_plan` call

**For top-level plan**:
```python
_force_reregister = bool((self._solve_context or {}).get("dissonance"))
if self._plan_changed(self._last_registered_top_plan, self._current_goal, self._plan_steps, force=_force_reregister):
    await self.brain.register_plan(goal=self._current_goal, steps=self._plan_steps)
    self._last_registered_top_plan = {"goal": self._current_goal, "steps": self._plan_steps}
else:
    # Emit skip event if orchestrator trace callback available
    if self._emit_trace_event_cb:
        self._emit_trace_event_cb("plan_registration_skipped", {
            "type": "top_level",
            "goal": self._current_goal,
            "reason": "plan unchanged",
        })
```

**For chunk plan**: same pattern with `_last_registered_chunk_plan`.

### Step 4: Check if `_emit_trace_event_cb` exists

If `SolveEngine` doesn't already have a trace callback attribute, check B138 (which adds this). For this card, gracefully handle the case where the callback is None/absent — the skip guard still fires, just without a trace event.

```python
_emit = getattr(self, "_emit_trace_event_cb", None)
if _emit:
    _emit("plan_registration_skipped", {...})
```

### Step 5: Force re-register on dissonance

On `dissonance=True`, pass `force=True` to `_plan_changed()` so the plan is re-registered even if goal/steps appear unchanged. This ensures the brain ledger gets a fresh timestamp after a plan reset.

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/solver.py` | Add `_last_registered_top_plan`, `_last_registered_chunk_plan` to `__init__`; add `_plan_changed()` helper; wrap both `register_plan` calls with idempotency gate |

## Validation Commands

```bash
# Run 15-step smoke test
ARC_API_KEY="$(jq -r '.key' benchmarks/arc3/.arc/arc.json)" \
  .venv/bin/python run_single_puzzle.py --puzzle-id 007bbfb7 --max-steps 15

# Count register_plan calls — should be ≤4 total (initial ×2 + force re-register on dissonance ×≤2)
jq '[.[] | select(.event == "register_plan" or (.data.method // "" | contains("register_plan")))] | length' master_timeline.json

# Confirm plan_registration_skipped events appear
jq '[.[] | select(.event == "plan_registration_skipped")] | length' master_timeline.json

# Run tests
cd sidequests-brain && .venv/bin/pytest -q tests/
```

## Acceptance Criteria (checklist)

- [ ] `register_plan` calls drop from 12+ per run to ≤4 (initial + forced re-registers only)
- [ ] `plan_registration_skipped` events appear in trace on unchanged-plan steps
- [ ] `dissonance=True` forces a re-register regardless of goal/steps identity
- [ ] `_last_registered_top_plan` and `_last_registered_chunk_plan` in `SolveEngine.__init__`
- [ ] `pytest -q tests/` passes

## Notes / Risks

- If `SolveEngine` doesn't have a single `register_plan` call site but multiple scattered through solve logic, audit with `grep -n "register_plan" agents/arc3/solver.py` first.
- Goal strings must match exactly for the dedup check. If goals are generated dynamically with floating point (e.g., `dist=16.523456`), normalize before comparison: `round(dist, 1)`.
- This card is independent of B138 (trace visibility). Implement both independently.
