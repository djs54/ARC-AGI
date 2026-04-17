# B-205 — Evolving Perceive Questions: Implementation Plan

- **Card:** backlog/B205.md
- **Priority:** P2
- **Dependencies:** B202 complete (`perceive_step_response()` must exist)

## Summary

Enrich the `notify_turn` content in `perceive_step_response()` with already-known orchestrator state (archetype, victory condition, active chunk, prior action outcome). No new LLM calls. The impact is that SideQuests ingests semantically richer context on every step, improving future retrieval quality.

## Technical Approach

### Single method change: `perceive_step_response()` in `agents/arc3/orchestrator.py`

The only file that changes is `orchestrator.py`. The modification is inside the `response_summary` construction block of `perceive_step_response()` (added by B202).

**Before** (B202 version):
```python
response_summary = (
    f"[STEP RESPONSE] Step {step}, action={action_id}. "
    f"State: {state}. Reward: {reward}. Done: {done}. "
    f"Grid: {delta_summary.get('n_cells_changed', 0)} cells changed"
    f"{', ' + str(delta_summary['apparent_effect']) if delta_summary.get('apparent_effect') else ''}."
    ...
)
```

**After** (B205 version):

Replace the `response_summary` construction with a context-aware builder:

```python
# Read already-available orchestrator context — no new queries
solve_ctx = self._solve_context or {}
archetype = solve_ctx.get("archetype") or "unknown"
archetype_conf = round(float(solve_ctx.get("archetype_confidence") or 0.0), 2)
victory = solve_ctx.get("victory_condition") or {}
victory_type = victory.get("type", "unknown") if isinstance(victory, dict) else "unknown"
victory_conf = round(float(victory.get("confidence", 0.0) if isinstance(victory, dict) else 0.0), 2)
active_chunk = solve_ctx.get("active_chunk") or {}
chunk_desc = active_chunk.get("description") or "none"

# Interpret reward signal
if reward is None:
    outcome_label = "outcome unknown"
elif reward > 0:
    outcome_label = f"reward={reward} (progress)"
else:
    outcome_label = "no progress"

# Determine delta interpretation
n_changed = delta_summary.get("n_cells_changed", 0)
apparent_effect = delta_summary.get("apparent_effect")
direction = delta_summary.get("direction")
delta_str = f"{n_changed} cells changed"
if apparent_effect:
    delta_str += f", {apparent_effect}"
if direction:
    delta_str += f", direction={direction}"
if n_changed == 0:
    delta_str = "no grid change"

# Build evolved question (step 0 = discovery, step > 0 = hypothesis testing)
if step == 0 or archetype == "unknown":
    phase_question = "What kind of puzzle is this and what is the likely win condition?"
else:
    phase_question = (
        f"Did {action_id or 'this action'} advance toward the victory condition? "
        f"Archetype={archetype} (conf={archetype_conf}), chunk={chunk_desc}."
    )

# Compose context-aware summary
if step == 0 or archetype == "unknown":
    # Bootstrap/discovery framing
    response_summary = (
        f"[STEP RESPONSE] Step {step}, action={action_id}. "
        f"State: {state}. Reward: {reward}. Done: {done}. "
        f"Grid: {delta_str}. "
        f"Available actions: {', '.join(available_actions) if available_actions else 'pending'}."
    )
else:
    # Hypothesis-testing framing
    response_summary = (
        f"[STEP RESPONSE] Step {step}, action={action_id}. "
        f"Puzzle type: {archetype} (conf={archetype_conf}). "
        f"Victory: {victory_type} (conf={victory_conf}). "
        f"Strategy: {chunk_desc}. "
        f"State: {state}. {outcome_label}. "
        f"Grid: {delta_str}. "
        f"New colors: {delta_summary.get('new_colors', [])}. "
        f"Available actions: {', '.join(available_actions) if available_actions else 'pending'}."
    )
```

Then add `phase_question` to the returned `perception` dict:

```python
perception = {
    "step": step,
    "state": state,
    "reward": reward,
    "done": done,
    "delta": delta_summary,
    "available_actions": available_actions,
    "active_colors": color_set,
    "phase_question": phase_question,        # NEW
    "archetype": archetype,                  # NEW — for downstream use
    "chunk_desc": chunk_desc,               # NEW — for downstream use
}
self._last_response_perception = perception
```

That's the complete change. No other files need modification for the core impact.

### Optional: Surface phase_question in _phase_answer_for (runner.py, line 1618)

B204 already updates `_phase_answer_for` to read `_last_response_perception`. With B205 adding `phase_question` to that dict, the answer can optionally include it:

```python
if phase == SolvePhase.PERCEIVE.value:
    perception = getattr(orchestrator, "_last_response_perception", None)
    if perception and perception.get("step", 0) > 0:
        delta = perception.get("delta", {})
        # ... existing delta/state/reward string ...
        question = perception.get("phase_question", "")
        if question:
            answer += f" Q: {question}"
        return answer
    return "Initial observation captured and memory retrieval seeded."
```

This is optional — implement only if B204 is already done.

## Concrete File Changes

| File | Lines | Change |
|------|-------|--------|
| `agents/arc3/orchestrator.py` | Inside `perceive_step_response()` (~line 1318+) | Replace `response_summary` construction with context-aware version (~30 lines); add `phase_question`, `archetype`, `chunk_desc` keys to returned `perception` dict |
| `agents/arc3/runner.py` | ~1618 (optional, if B204 done) | Surface `phase_question` from `_last_response_perception` in `_phase_answer_for` |

## Test Updates

**`tests/test_arc3_orchestrator.py`** — update `test_perceive_step_response` (added by B202):

Add a variant that sets `orchestrator._solve_context` with known archetype/chunk before calling `perceive_step_response()`, then asserts that `notify_turn` content includes archetype name and chunk description.

```python
async def test_perceive_step_response_with_context(mock_brain, sample_observation):
    orch = ARCOrchestrator(mock_brain, MockLLM(), ...)
    orch._solve_context = {
        "archetype": "space",
        "archetype_confidence": 0.57,
        "victory_condition": {"type": "unknown", "confidence": 0.10},
        "active_chunk": {"description": "Plateau Exploitation: ACTION2"},
    }
    sample_observation["state"] = "NOT_FINISHED"
    sample_observation["available_actions"] = ["ACTION1", "ACTION2"]

    result = await orch.perceive_step_response(
        sample_observation, step=5, reward=0.0, done=False, action_id="ACTION2"
    )

    call_content = mock_brain.notify_turn.call_args[1].get("content", "")
    assert "space" in call_content           # archetype name present
    assert "Plateau Exploitation" in call_content  # chunk desc present
    assert "conf=0.57" in call_content       # confidence present
    assert result["phase_question"] != "What kind of puzzle is this"  # evolved
    assert result["archetype"] == "space"
```

## Validation Commands

```bash
# Run orchestrator tests
pytest tests/test_arc3_orchestrator.py -v -k "perceive"

# Smoke test — check enriched perceive content
python run_single_puzzle.py
python3 -c "
import json
d = json.load(open('master_timeline.json'))
perceive_entries = [e for e in d if e.get('phase') == 'perceive' and e.get('name') == 'notify_turn']
for p in perceive_entries[:5]:
    print(p.get('what', '')[:200])
    print()
"
```

Expected: after step 0, perceive notify_turn entries show puzzle type, strategy, and outcome framing instead of just state/reward/delta.

## Acceptance Criteria
See backlog/B205.md.

## Risks

- **_solve_context may be empty at step 1-2:** Archetype classifies gradually. The code falls back to discovery framing when `archetype == "unknown"`, so early steps are safe.
- **chunk_desc may be None:** Already guarded with `or "none"` fallback.
- **No LLM calls:** All context is from in-memory orchestrator fields — `_solve_context`, `_step_history`. This is a pure string assembly operation.
