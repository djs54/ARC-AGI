# Plan for B141 — Enforce No-Progress Bail-Out Escalation in Orchestrator Loop

## Card Metadata

- **Card ID**: B141
- **Priority**: P0
- **Dependencies**: None

## Summary

The orchestrator increments `_consecutive_no_progress_steps` correctly but nothing acts on it. The loop detector at ~L758 triggers at `≥ 2` but only queries memory — no exit, no replan, no action block. This plan introduces a 3-tier escalation that converts the counter into corrective action.

## Verified Baseline

From `live_gemma4_e4b_timeout_1775221087`:
- 15 consecutive steps with `reward = 0.0`
- `_consecutive_no_progress_steps` reached 15 — never consumed
- Guard status escalated to "warned" but never "blocked"
- Same ACTION6 dispatched every step
- `dissonance_detected` stayed `False` because chunk graduation remained high

## Technical Approach

### 1. Wire 3-tier escalation into `_should_check_loop` block

In `agents/arc3/orchestrator.py`, after the existing memory query at ~L758-763, add escalation logic keyed on `_consecutive_no_progress_steps`:

```python
# Tier 1: Force replan
if self._consecutive_no_progress_steps >= 3:
    self._solve_context["dissonance_detected"] = True
    self._trace_event("no_progress_escalation", tier=1, action="force_replan",
                      steps=self._consecutive_no_progress_steps)

# Tier 2: Block current action type
if self._consecutive_no_progress_steps >= 5:
    self._blocked_actions.add(current_action_type)
    self._trace_event("no_progress_escalation", tier=2, action="block_action",
                      blocked=current_action_type,
                      steps=self._consecutive_no_progress_steps)

# Tier 3: Abandon chunk
if self._consecutive_no_progress_steps >= 8:
    self._mark_active_chunk_failed("no_progress_abandon")
    self._trace_event("no_progress_escalation", tier=3, action="abandon_chunk",
                      steps=self._consecutive_no_progress_steps)
    self._consecutive_no_progress_steps = 0  # reset ladder
```

### 2. Reset counter on escalation trigger

Each tier that fires resets the counter so the escalation ladder restarts if the new strategy also stalls. The reset happens at tier 3 (abandon), since tiers 1 and 2 are cumulative (replan at 3, then block at 5, then abandon at 8).

### 3. Ensure trace visibility

Each escalation emits a trace event with:
- `escalation_tier` (1/2/3)
- `action_taken` (force_replan / block_action / abandon_chunk)
- `no_progress_steps` at time of trigger
- `blocked_action` (tier 2 only)

These appear in `agent_execution_trace.json` under the step's event list.

## Concrete File Changes

### `agents/arc3/orchestrator.py`
- ~L758-770: Add 3-tier escalation after existing `_should_check_loop` memory query
- ~L2554-2557: Ensure the counter increment block doesn't interfere with the new reset logic
- Add `_blocked_actions: set` field to `__init__` if it doesn't exist
- Add `_mark_active_chunk_failed(reason)` helper if it doesn't exist (delegate to existing chunk lifecycle methods)
- Wire `_blocked_actions` into action selection to skip blocked action types

### `tests/test_b141_no_progress_bailout.py` (new)
- Test tier 1: After 3 zero-reward steps, verify `dissonance_detected` is `True`
- Test tier 2: After 5 zero-reward steps, verify the current action type is in `_blocked_actions`
- Test tier 3: After 8 zero-reward steps, verify active chunk is marked `failed` and counter resets
- Test reset: After tier 3 fires and counter resets, verify a new 3-step run triggers tier 1 again
- Test no false positives: A step with `reward > 0` resets the counter and no escalation fires

## API/Schema/Test Updates

- No tool catalog changes
- No adapter allow-list changes
- No schema changes

## Acceptance Criteria

- [ ] At 3 consecutive zero-reward steps, `dissonance_detected` flips to `True`
- [ ] At 5 consecutive zero-reward steps, the current action type is blocked
- [ ] At 8 consecutive zero-reward steps, the active chunk is marked `failed` and counter resets
- [ ] Escalation events visible in `agent_execution_trace.json`
- [ ] Existing test suites pass with no regressions

## Validation Commands

```bash
.venv/bin/python -m pytest tests/test_b141_no_progress_bailout.py -q
.venv/bin/python -m pytest tests/ -q --timeout=60
```

## Risks / Constraints

- The `_blocked_actions` set must be checked in `act()` or `_infer_action6_coordinates()` — need to verify the exact action selection path
- Tier 3 chunk abandonment must not crash if there is no active chunk (guard with `if self._active_chunk:`)
- The counter reset at tier 3 means escalation restarts from scratch — this is intentional to avoid permanent lockout

## Done When

- All 3 tiers fire at the correct thresholds in unit tests
- The escalation events appear in trace output
- Existing ARC test suites pass without regression
