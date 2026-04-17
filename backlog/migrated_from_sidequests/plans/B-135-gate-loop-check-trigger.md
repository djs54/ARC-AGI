# B135 Plan: Replace Hardcoded `step_num > 3` Loop Check With Evidence-Based Gate

## Summary

`orchestrator.py` line ~750 has `if step_num > 3:` guarding a `current_truth("Am I looping?")` call. This fires on 12 of 15 steps in a typical run with 11 false-positive calls. This plan replaces the integer threshold with a compound evidence gate.

## Current Code

```python
# orchestrator.py ~line 748-754
if step_num > 3:
    loop_check_result = await self._check_for_loop(step_num)
    if loop_check_result.get("loop_detected"):
        self._hypothesis_context["loop_detected"] = True
```

## Technical Approach

### Step 1: Identify existing tracking attributes

Before writing new code, search `orchestrator.py` for:
- `_consecutive_no_progress` or similar counter
- `_frame_hashes` or `_recent_frames` list
- `_hypothesis_context["dissonance"]` or `_solve_context["dissonance"]`

Use what exists; do not duplicate tracking.

### Step 2: Compose the evidence gate

Replace the `if step_num > 3:` line with:

```python
# Evidence-based loop check gate
_no_progress = getattr(self, "_consecutive_no_progress_steps", 0)
_frame_dupe = len(self._recent_frame_hashes) != len(set(self._recent_frame_hashes)) if hasattr(self, "_recent_frame_hashes") else False
_dissonance = bool((self._solve_context or {}).get("dissonance") or (self._hypothesis_context or {}).get("dissonance"))

_should_check_loop = (
    _no_progress >= 2
    or _frame_dupe
    or _dissonance
)

if _should_check_loop:
    loop_check_result = await self._check_for_loop(step_num)
    if loop_check_result.get("loop_detected"):
        self._hypothesis_context["loop_detected"] = True
else:
    self._emit_trace_event("loop_check_skipped", {
        "step": step_num,
        "reason": "no evidence of loop (no_progress={}, frame_dupe={}, dissonance={})".format(
            _no_progress, _frame_dupe, _dissonance
        ),
    })
```

### Step 3: Ensure `_recent_frame_hashes` exists

If `_recent_frame_hashes` is not tracked, add it in the step iteration loop:

```python
# After receiving ARC response, append current frame hash
frame_hash = current_frame.get("hash") or hash(str(current_frame))
if not hasattr(self, "_recent_frame_hashes"):
    self._recent_frame_hashes = []
self._recent_frame_hashes.append(frame_hash)
# Keep last 5 only
self._recent_frame_hashes = self._recent_frame_hashes[-5:]
```

If frame hash tracking already exists under a different name, use it; do not add duplicate tracking.

### Step 4: Remove `step_num > 3` entirely

After replacement, confirm the old `if step_num > 3:` line is gone. Do not leave it commented out.

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/orchestrator.py` | Replace `if step_num > 3:` with compound evidence gate; add `loop_check_skipped` event; optionally add `_recent_frame_hashes` tracking if not already present |

## Validation Commands

```bash
# Run 15-step smoke test
ARC_API_KEY="$(jq -r '.key' benchmarks/arc3/.arc/arc.json)" \
  .venv/bin/python run_single_puzzle.py --puzzle-id 007bbfb7 --max-steps 15

# Count loop_check calls — should be ≤3 on a non-looping puzzle
jq '[.[] | select(.event == "loop_check")] | length' master_timeline.json

# Count skipped events
jq '[.[] | select(.event == "loop_check_skipped")] | length' master_timeline.json

# Confirm loop_detected only fires with evidence
jq '[.[] | select(.event == "loop_check" and .data.loop_detected == true)] | .[].data' master_timeline.json

# Run tests
cd sidequests-brain && .venv/bin/pytest -q tests/
```

## Acceptance Criteria (checklist)

- [ ] `loop_check` events in trace: ≤3 for a 15-step run on a clean puzzle
- [ ] `loop_check_skipped` event fires on all bypassed steps with reason field
- [ ] Loop check fires when `consecutive_no_progress >= 2`
- [ ] Loop check fires when frame hash duplicate detected
- [ ] No `if step_num > 3` in codebase after this change
- [ ] `pytest -q tests/` passes

## Notes / Risks

- If `_consecutive_no_progress_steps` doesn't exist yet, add it in the step outcome processing block — but keep the implementation minimal and scoped to this card.
- The `_recent_frame_hashes` window of 5 is sufficient to detect short cycles. Don't over-engineer.
- Do not change the internal logic of `_check_for_loop` itself — only change when it's called.
