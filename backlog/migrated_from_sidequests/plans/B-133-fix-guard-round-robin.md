# B133 Plan: Fix Guard Policy Round-Robin — Gate Override on LLM Decision Quality

## Summary

`_enforce_action_policy()` overrides the LLM's action choice whenever the proposed action has been tried before, regardless of whether:
- The LLM's decision was actually valid (vs. a crash fallback)
- The board state is different from when the action was last tried

This produces deterministic 1→2→3→4→5 cycling. This plan replaces the naive "tried before → override" logic with a context-aware gate.

**Prerequisite**: B132 must be complete (LLM is live) before validating this card's acceptance criteria. Implementation can proceed in parallel.

## Current Behavior (from master_timeline.json)

```
step 1: LLM proposes ACTION1 → guard: first try → accepted
step 2: LLM proposes ACTION1 (fallback) → guard: already tried → overrides to ACTION2
step 3: LLM proposes ACTION1 (fallback) → guard: already tried → overrides to ACTION3
...
step 6: guard resets explored set → back to ACTION1
```

## Technical Approach

### Step 1: Add `llm_decision_source` tracking

In `_mental_sandbox()` (or wherever the LLM response is parsed), set a flag on the decision:

```python
decision = {
    "action": chosen_action,
    "source": "llm",          # or "fallback" if parse failed
    "confidence": score,
}
```

Pass this dict through to `_enforce_action_policy()`.

### Step 2: Gate override logic on decision source

In `_enforce_action_policy()`, change the condition:

**Before (pseudocode)**:
```python
if action in self._tried_actions:
    action = next_unexplored()
```

**After**:
```python
if decision["source"] == "fallback":
    # LLM didn't produce a real decision; don't trust it enough to override with guard
    self._emit_trace_event("guard_skipped_fallback", {"reason": "LLM produced fallback; guard not applied"})
    return decision["action"]  # let fallback through; B132 will fix the root cause

if action in self._tried_actions and frame_hash == self._action_frame_hashes.get(action):
    # Same action AND same board state → genuine repetition → override justified
    override_action = next_unexplored()
    self._emit_trace_event("guard_override_reason", {
        "original": action,
        "override": override_action,
        "reason": "repeated action on identical frame state",
        "frame_hash": frame_hash,
    })
    return override_action

# LLM chose a repeated action but board state changed → trust the LLM
return action
```

### Step 3: Track frame hash per action

Add `self._action_frame_hashes: dict[str, str] = {}` to orchestrator state. After each ARC API call returns, store:

```python
self._action_frame_hashes[action] = current_frame_hash
```

### Step 4: Add `llm_decision_source` to trace events

In the act phase trace events, include:
```python
self._emit_trace_event("act_decision", {
    "action": action,
    "llm_decision_source": decision.get("source", "unknown"),
    "guard_applied": was_overridden,
})
```

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/orchestrator.py` | Add `_action_frame_hashes` dict; modify `_enforce_action_policy` with source gate + frame-hash dedup; add `guard_override_reason` and `guard_skipped_fallback` trace events; add `llm_decision_source` to act trace |

## Validation Commands

```bash
# After B132 is complete, run 5-step smoke test
ARC_API_KEY="$(jq -r '.key' benchmarks/arc3/.arc/arc.json)" \
  .venv/bin/python run_single_puzzle.py --puzzle-id 007bbfb7 --max-steps 5

# Check: no more sequential 1,2,3,4,5 cycling
jq '[.[] | select(.event == "arc_action_submitted") | .data.action] | @json' master_timeline.json

# Check: all actions have llm_decision_source field
jq '[.[] | select(.event == "act_decision") | .data.llm_decision_source] | unique' master_timeline.json

# Guard override events should only fire with frame_hash evidence
jq '[.[] | select(.event == "guard_override_reason")] | length' master_timeline.json

# Run tests
cd sidequests-brain && .venv/bin/pytest -q tests/
```

## Acceptance Criteria (checklist)

- [ ] B132 is complete and validated first
- [ ] Zero `guard_skipped_fallback` events in a run where LLM is healthy
- [ ] `guard_override_reason` events include `frame_hash` evidence
- [ ] Action sequence shows LLM-driven diversity (not `1,2,3,4,5` cycling)
- [ ] Each `act_decision` event has `llm_decision_source` field
- [ ] `pytest -q tests/` passes

## Notes / Risks

- The `_action_frame_hashes` dict grows unbounded per run (max 5 entries for 5 actions — negligible).
- If the board genuinely requires trying `ACTION2` twice (same state), the guard will still override. This is acceptable for now; improve with dissonance scoring in B136.
- This card scopes only `_enforce_action_policy`. Do not refactor other guard logic.
