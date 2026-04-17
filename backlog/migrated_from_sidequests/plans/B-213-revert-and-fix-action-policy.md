# B-213 — Revert Puzzle-Specific Heuristics and Fix Action/Autopilot Override Failures

- **Card:** backlog/B213.md
- **Priority:** P0
- **Dependencies:** None

## Summary

Three surgical changes to `agents/arc3/orchestrator.py`:
1. Remove `_detect_split_map_rotate_cross()` and all three call sites (pure removal, ~100 lines)
2. Add an exploration-intent bypass before the decay guard in `_enforce_action_policy()` (~12 lines)
3. Add an autopilot no-progress confidence gate in `_try_autopilot()` (~15 lines)

Plus targeted tests in `tests/test_arc3_orchestrator.py`.

## Part A: Remove `_detect_split_map_rotate_cross` and call sites

### Step 1: Remove the method body (line ~4763)

Delete the entire `_detect_split_map_rotate_cross` method (lines 4763–4840, approximately).

### Step 2: Remove call site in `_enforce_action_policy` (line ~4180)

The block to remove:
```python
# If the board looks like split sections + rotation trigger and we are
# stuck, explicitly try interact (ACTION5) before continuing movement.
rotate_hint = self._detect_split_map_rotate_cross(observation or {})
if (
    rotate_hint
    and "ACTION5" in available_actions
    and self._consecutive_no_progress_steps >= 2
    and action.get("action_id") != "ACTION5"
):
    prior_action = action.get("action_id")
    action.update({
        "action_id": "ACTION5",
        "rationale": (...),
        "decision_source": "policy_override",
        "override_reason": "rotate_cross_inference",
        "adherence_ok": False,
    })
    return action
```

Remove this block entirely. Nothing replaces it.

### Step 3: Remove call site in `perceive_step_response`

The block to remove (injecting "rotate trigger cross" into response_summary):
```python
rotate_hint = self._detect_split_map_rotate_cross(observation)
if rotate_hint:
    response_summary += (
        f"Detected {rotate_hint['section_count']} major sections and a likely rotate trigger ..."
    )
```

Remove this block. The `response_summary` construction otherwise stays.

### Step 4: Remove call site in `_try_autopilot`

The block to remove:
```python
rotate_hint = self._detect_split_map_rotate_cross(observation)
if rotate_hint and self._consecutive_no_progress_steps >= 1:
    cross_coord = (int(rotate_hint["cross_x"]), int(rotate_hint["cross_y"]))
    if cross_coord not in recent_coords:
        ...
        return cross_coord
```

Remove this block. The autopilot's existing stagnation logic remains.

### Step 5: Remove retrieval query injection in `_enforce_action_policy`

The block to remove:
```python
rotate_hint = self._detect_split_map_rotate_cross(observation)
if rotate_hint:
    additional_queries.extend([
        "rotate trigger cross",
        "ACTION5 interact rotation",
    ])
```

Remove this block.

---

## Part B: Fix exploration-intent bypass in `_enforce_action_policy`

**Location:** Inside `_enforce_action_policy`, immediately before the `_should_skip_chunk_action` block (currently at line ~4220).

The current flow:
```python
# [line ~4209] autopilot early-return
if source == "autopilot":
    ...
    return action

# [line ~4218] THIS is where we need to insert the guard:
chosen_effect = observed_effects.get(action_id)
allow_repeat_probe = self._normalize_action_id(action_id) == "ACTION6"
if action_id in available_actions and self._should_skip_chunk_action(chosen_effect) and not allow_repeat_probe:
    # ... replacement selection ...
```

**Insert before `chosen_effect = observed_effects.get(action_id)`:**

```python
# Exploration-intent bypass: never let the decay guard override an action
# the LLM is explicitly choosing to explore. Exploration decisions are
# higher-authority than fatigue state from prior steps.
_exploration_keywords = (
    "haven't tried", "not yet tried", "new action", "unexplored",
    "never tried", "hasn't been tried", "want to see",
)
_is_exploration_intent = action_id in unexplored or any(
    kw in rationale.lower() for kw in _exploration_keywords
)
if _is_exploration_intent:
    self._emit_trace_event(
        "operation",
        "guard_exploration_bypass",
        {"action": action_id, "source": source},
        {"reason": "LLM chose exploration; bypassing decay guard"},
    )
    return action
```

This is inserted as a new block. The rest of the function is unchanged.

---

## Part C: Fix autopilot spatial lock under no-progress

**Location:** Inside `_try_autopilot`, before returning a coordinate selected from intermediate/goal mapping.

Add a confidence gate:

```python
repeat_family = target_family in recent_target_families
if self._consecutive_no_progress_steps >= 2 and repeat_family:
    self._emit_trace_event(
        "operation",
        "autopilot_confidence_drop",
        {"family": target_family, "coord": candidate_coord},
        {"reason": "no_progress_spatial_lock"},
    )
    return None
```

Behavioral intent:
- Prevent repeated low-confidence geometric continuation after zero reward.
- Fall back to planner/policy exploration instead of burning more navigation steps.
- Keep autopilot active when progress resumes.

---

## Tests

### Update existing test
In `tests/test_arc3_orchestrator.py`, the existing `test_enforce_action_policy_overrides_stale_low_value_repeat` test should still pass because that test uses a non-exploration LLM rationale and a non-unexplored action.

### Add new test
```python
def test_enforce_action_policy_preserves_unexplored_exploration(mock_brain, sample_observation):
    """Decay guard must not override when LLM is choosing an unexplored action."""
    orch = ARCOrchestrator(mock_brain, MockLLM(), ...)

    # Set up: ACTION4 is unexplored, but has stale observed_effects from a prior attempt
    orch._hypothesis_context = {
        "observed_action_effects": [
            {
                "action": "ACTION4",
                "value_status": "low_value",
                "zero_reward_streak": 5,
                "rank_score": 0.1,
            }
        ],
        "unexplored_actions": ["ACTION4"],
    }

    action = {
        "action_id": "ACTION4",
        "rationale": "ACTION4 is a new action that hasn't been tried yet in this context.",
        "decision_source": "llm",
    }

    result = orch._enforce_action_policy(
        action, ["ACTION1", "ACTION2", "ACTION3", "ACTION4"], None
    )

    assert result["action_id"] == "ACTION4", (
        "Decay guard must not override an unexplored action the LLM chose to explore"
    )
    assert result.get("decision_source") != "policy_override"


def test_detect_split_map_rotate_cross_does_not_exist():
    """Puzzle-specific heuristic must be removed."""
    import agents.arc3.orchestrator as orch_module
    assert not hasattr(orch_module.ARCOrchestrator, "_detect_split_map_rotate_cross"), (
        "_detect_split_map_rotate_cross is a puzzle-specific cheat code and must not exist"
    )


def test_autopilot_confidence_drops_on_no_progress_spatial_lock(mock_brain, sample_observation):
    """Autopilot must bail out when no-progress repeats the same target family."""
    orch = ARCOrchestrator(mock_brain, MockLLM(), ...)
    orch._consecutive_no_progress_steps = 3
    orch._recent_autopilot_target_families = ["intermediate", "intermediate"]

    result = orch._try_autopilot(sample_observation, solve_context={...})

    assert result is None
```

---

## Validation Commands

```bash
# Core policy tests
pytest tests/test_arc3_orchestrator.py -v -k "policy"

# Full orchestrator suite
pytest tests/test_arc3_orchestrator.py -v

# Smoke test — verify no "stale low-value" override of exploration
python run_single_puzzle.py
python3 -c "
import json
lines = open('submission_results_single.live.jsonl').readlines()
overrides = [l for l in lines if 'policy override: stale low-value' in l]
print(f'Override count: {len(overrides)}')
for l in overrides[:5]:
    d = json.loads(l)
    print(f'step={d[\"step\"]} action={d[\"action_id\"]} rat={d[\"rationale\"][:120]}')
"
```

## Risks

- **Test for rotate_cross in test_arc3_orchestrator.py:** GPT-5.4 added tests for `_detect_split_map_rotate_cross`. These must be deleted alongside the method. Search for "rotate_cross" and "rotate_hint" in the test file and remove those test cases.
- **perceive_step_response removal:** The `perceive_step_response` method itself was added by this session's B202 work and should be **kept**. Only remove the `rotate_hint` injection block inside it, not the method.
