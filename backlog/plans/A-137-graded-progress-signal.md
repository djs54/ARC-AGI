# Plan: A137 — Graded Progress Signal (levels_completed + grid delta)

## Context

The ARC-AGI-3 API returns `levels_completed` and `win_levels` in every FrameResponse (see `benchmarks/arc3/interface_contract.md`, "FrameResponse required fields"). `normalize_observation` in `benchmarks/arc3/adapter.py` already passes them through (lines ~1143-1144):

```python
"levels_completed": raw.get("levels_completed"),         # B157
"win_levels": raw.get("win_levels"),                     # B157
```

But the game session in `run_single_puzzle.py` discards them when computing reward:

```python
# run_single_puzzle.py, _ArcV2GameSession.execute_action, real-API branch (~line 409-419)
frame_response = _unwrap_arc_game_payload(response.json())
reward = 1.0 if frame_response.get("state") == "WIN" else 0.0
done = frame_response.get("state") in ("WIN", "GAME_OVER")
return {
    "observation": ARC3Adapter(...).normalize_observation(frame_response),
    "did_progress": reward >= 1.0,
    "actual_effect": frame_response.get("state") or frame_response.get("effect"),
    "reward": reward,
    "done": done,
    "state": frame_response.get("state"),
}
```

And the evaluator consumes only the boolean (`agents/arc4/evaluator.py` line 51):

```python
meaningful_progress = bool(execution.did_progress)
```

The executor (`agents/arc4/executor.py`, `_normalize_result` ~line 144-151) copies all non-`observation` keys from the transport result into `ExecutionResult.metadata`, so new keys returned by `execute_action` automatically appear in `execution.metadata` — no executor changes needed for plumbing.

## Implementation Steps

### Step 1: Track previous level count in `_ArcV2GameSession`

**File:** `run_single_puzzle.py`, class `_ArcV2GameSession`

Add instance state in `__init__`: `self._prev_levels_completed: int = 0` and `self._prev_grid_hash: str | None = None`.

In `open()` (real-API branch, after `payload = _unwrap_arc_game_payload(...)`): set `self._prev_levels_completed = int(payload.get("levels_completed") or 0)`. Same in the mock branch using the mock frame.

### Step 2: Compute graded progress in `execute_action`

**File:** `run_single_puzzle.py`, both the real-API branch and the mock branch of `execute_action`.

Replace the reward computation with a helper (module-level function so it is unit-testable):

```python
def _compute_progress(frame_response: Mapping[str, Any], prev_levels: int, prev_grid_hash: str | None, new_grid_hash: str | None) -> dict[str, Any]:
    state = frame_response.get("state")
    levels = int(frame_response.get("levels_completed") or 0)
    level_gain = max(0, levels - prev_levels)
    win = state == "WIN"
    grid_changed = bool(new_grid_hash and prev_grid_hash and new_grid_hash != prev_grid_hash)
    progress_reward = 1.0 if win else float(level_gain)
    return {
        "reward": progress_reward,
        "did_progress": win or level_gain > 0,
        "levels_completed": levels,
        "prev_levels_completed": prev_levels,
        "level_gain": level_gain,
        "grid_changed": grid_changed,
    }
```

In `execute_action`: compute `new_grid_hash` from the normalized observation's `frame_hash` field (already produced by `normalize_observation` via `StateNode.hash_grid`), call `_compute_progress`, merge its dict into the returned mapping, then update `self._prev_levels_completed` and `self._prev_grid_hash`. Keep `done` logic unchanged.

### Step 3: Teach the evaluator the three tiers

**File:** `agents/arc4/evaluator.py`, method `evaluate`

After line 51 (`meaningful_progress = bool(execution.did_progress)`), read the new metadata:

```python
exec_meta = execution.metadata if isinstance(execution.metadata, Mapping) else {}
level_gain = int(exec_meta.get("level_gain") or 0)
grid_changed_flag = exec_meta.get("grid_changed")
```

In the decision block (currently lines ~88-99), change the falsification branch: when prediction is falsified BUT `grid_changed_flag is True`, set `falsification_delta = 0` and `reason = "effect_without_progress"` instead of `falsification_delta = 1` / `"prediction_falsified"`. Keep `falsification_delta = 1` when the grid is unchanged (the action truly did nothing). Do not change PIVOT threshold logic — `repeated_falsification` still triggers off accumulated deltas.

Add to evaluation metadata: `"level_gain": level_gain`, `"progress_tier": "level" if level_gain else ("grid_change" if grid_changed_flag else "flat")`.

Note: the evaluator already has its own `grid_unchanged` computation (A133, around lines 53-76 via perception grid hash). Prefer `grid_changed_flag` from execution metadata when present; fall back to the existing A133 logic when absent (mock transports in old tests don't provide it).

### Step 4: Telemetry progress_class

**File:** `agents/arc4/telemetry.py`

Find where `progress_class` is emitted (search `progress_class`). Map from the evaluator's `progress_tier` metadata: `level` → `"level"`, `grid_change` → `"grid_change"`, else `"flat"`. Keep emitting `progress_reward` from execution metadata `reward`.

### Step 5: Temporal parity

**File:** `agents/arc4/temporal_workflows.py`

The workflow reads `evaluation.get("meaningful_progress")` and `falsification_delta` from the activity result dicts — these come from the same evaluator, so no logic change needed. Verify by grep that the workflow does not re-derive progress from `reward` anywhere; if it does, align it.

### Step 6: Tests

**File:** `tests/test_a137_graded_progress_signal.py` (new)

1. `test_compute_progress_level_gain` — prev_levels=1, frame levels_completed=2 → did_progress True, level_gain 1, reward 1.0
2. `test_compute_progress_win` — state WIN → did_progress True regardless of levels
3. `test_compute_progress_flat_same_grid` — same hash, same levels → did_progress False, grid_changed False
4. `test_compute_progress_grid_changed_only` — different hash, same levels → did_progress False, grid_changed True
5. `test_evaluator_no_falsification_when_grid_changed` — execution.metadata grid_changed=True, did_progress=False → falsification_delta == 0, reason == "effect_without_progress"
6. `test_evaluator_falsifies_when_no_effect` — grid_changed=False → falsification_delta == 1
7. `test_evaluator_level_gain_is_meaningful_progress` — did_progress=True via level gain → decision CONTINUE, reason "meaningful_progress"

Build `ExecutionResult` fixtures directly (see `tests/test_arc4_evaluator.py` for existing fixture patterns).

### Step 7: Verify

```bash
make test-a
.venv/bin/python -m pytest tests/test_a137_graded_progress_signal.py tests/test_arc4_evaluator.py tests/test_a131_a132_a133_workflow_behavior.py tests/test_a135_graph_driven_planning.py tests/test_a136_mechanic_prior_extraction.py -q
```

If an existing A133 test asserts `falsification_delta == 1` for a grid-changed case, update it and note the intentional behavior change in the card.

## Files Modified

| File | Change |
|------|--------|
| `run_single_puzzle.py` | `_compute_progress` helper; session tracks prev levels/grid hash; execute_action returns graded fields |
| `agents/arc4/evaluator.py` | Three-tier progress handling; no falsification on grid-changed steps |
| `agents/arc4/telemetry.py` | progress_class from progress_tier |
| `tests/test_a137_graded_progress_signal.py` | New, 7 tests |

## Conflict Note (for fan-out)

Touches `run_single_puzzle.py` and `agents/arc4/evaluator.py`. Conflicts with A138 (evaluator) and A143 (run_single_puzzle decomposition). Land A137 before A138; land A143 after both.

## Risks

- Some games may report `levels_completed` as null → `int(x or 0)` guards this.
- Games that animate every frame would never accumulate falsifications → PIVOT may fire less often. Acceptable: a grid change IS evidence the action does something; the A131 repeat-decay still rotates actions.
