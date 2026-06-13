# Plan: A141 — Mock Harness Parity With ARC API Contract

## Context

The mock lives in `benchmarks/arc3/harness.py` (`ARC3Harness._get_mock_initial_frame` lines 219-227, `_execute_mock_action` lines 237-254). It is consumed by:

- `run_single_puzzle.py` `_ArcV2GameSession.open()` (line ~396, non-real-api branch) and `execute_action` (line ~423)
- `agents/arc3/runner.py` (lines ~1568, ~3471) — v1 paths
- Many tests mock the harness themselves with `MagicMock` (grep `_get_mock_initial_frame` in `tests/`) — those won't break, but tests asserting WIN-at-step-N behavior of the real mock will.

FrameResponse required fields per `benchmarks/arc3/interface_contract.md`:
`game_id`, `guid`, `frame` (array of frames, each 64x64 ints 0..15), `state` (NOT_FINISHED | NOT_STARTED | WIN | GAME_OVER), `levels_completed` (int), `win_levels` (int), `action_input` (echo of triggering action), `available_actions` (array of action IDs).

## Implementation Steps

### Step 1: Define the scripted mock game

**File:** `benchmarks/arc3/harness.py` — add a module-level docstring-documented spec and a small state holder.

Game spec ("mock-lights"): a 64x64 grid, background 0, with three 4x4 colored blocks at fixed positions: block A color 3 at rows 10-13/cols 10-13, block B color 5 at rows 10-13/cols 30-33, block C color 7 at rows 40-43/cols 20-23. `win_levels = 2`. Rules:

- ACTION1: moves block A right by 2 cols (grid changes, no level)
- ACTION2: moves block A left by 2 cols (grid changes, no level)
- ACTION3: no-op (grid unchanged) — exists so falsification paths get exercised
- ACTION4: toggles block B color between 5 and 9 (grid changes)
- ACTION6 with x,y inside block C: increments `levels_completed` and recolors block C (level progress); outside: no-op
- When `levels_completed == win_levels`: state = WIN. Second level gained by clicking block C again after a recolor.
- `available_actions = [1, 2, 3, 4, 6]` on every frame.

Track per-(game_id, guid) mock state in a dict on the harness instance: `self._mock_games: Dict[str, dict] = {}` holding block positions, colors, levels.

### Step 2: Rewrite `_get_mock_initial_frame`

Build the 64x64 grid from the spec, return:

```python
{
    "game_id": game_id,
    "guid": f"guid-{game_id}",
    "frame": [grid],                      # one 64x64 frame
    "state": "NOT_STARTED",
    "levels_completed": 0,
    "win_levels": 2,
    "available_actions": [1, 2, 3, 4, 6],
    "action_input": None,
    "episode_num": 1,
    "step_num": 0,
}
```

Helper `_render_mock_grid(game_state) -> list[list[int]]` builds the grid from block positions/colors so `_execute_mock_action` reuses it.

### Step 3: Rewrite `_execute_mock_action`

Signature unchanged: `(game_id, action: Dict, step) -> Tuple[Dict, float, bool]`. Apply the rule table to the mock game state, re-render the grid, return the full FrameResponse-shaped dict (same required fields, `action_input` echoing the action, `step_num` incremented). Reward = `1.0` on WIN, else `float(level_gain)` (matches A137's grading if it has landed; if A137 hasn't landed, keep returning the frame and let the session compute). Remove the `step >= 4 → WIN` and `ACTION6 && step>=1 → WIN` shortcuts entirely.

### Step 4: Fix dependents of the old auto-WIN behavior

Run the full suite and triage failures. Known suspects:
- `tests/test_b185_failure_class_saturated.py` (uses `harness._get_mock_initial_frame.return_value = {...}` — MagicMock, unaffected)
- `tests/test_arc3_durable_runner.py` fixtures (MagicMock, unaffected)
- Any test invoking the REAL harness mock and asserting WIN within N steps — update to either play the scripted winning sequence (`ACTION6` at block C twice) or assert the new deterministic behavior.

### Step 5: Contract test

**File:** `tests/test_a141_mock_contract.py` (new)

```python
REQUIRED_FRAME_FIELDS = [
    # benchmarks/arc3/interface_contract.md "FrameResponse required fields"
    "game_id", "guid", "frame", "state", "levels_completed", "win_levels", "available_actions",
]
```

1. `test_initial_frame_has_required_fields`
2. `test_initial_frame_grid_is_64x64_int_0_15`
3. `test_action_frames_have_required_fields` — run each ACTION1-6 once, validate every response
4. `test_action1_changes_grid_action3_does_not`
5. `test_click_block_c_gains_level` — ACTION6 at (21, 41)→ levels_completed 1
6. `test_two_clicks_win` — second targeted click → state WIN
7. `test_no_auto_win_after_5_steps` — 6 ACTION3s → still NOT_FINISHED

### Step 6: v2 integration test (mock mode)

**File:** same test file. Spin the v2 workflow against the mock session (see `tests/test_a131_a132_a133_workflow_behavior.py` for orchestrator wiring patterns) and assert: planner candidate count > 1, at least one step with falsification (ACTION3), and that a scripted ACTION6-at-block-C execution yields did_progress (if A137 landed) or reward > 0.

### Step 7: Verify

```bash
make test-a
.venv/bin/python -m pytest tests/test_a141_mock_contract.py -q
PYTHONPATH=. .venv/bin/python run_single_puzzle.py --agent-version=v2 --num-puzzles 1 --max-steps 20 2>/dev/null
# then inspect submission_results_single.live.jsonl: candidates>1, mixed falsification
```

## Files Modified

| File | Change |
|------|--------|
| `benchmarks/arc3/harness.py` | Scripted mock game; contract-complete frames; remove auto-WIN |
| `tests/test_a141_mock_contract.py` | New, ~8 tests |
| (triage) various tests | Update any that depended on auto-WIN |

## Conflict Note (for fan-out)

Independent of A137-A140 file-wise (only touches `harness.py`), safe to run in parallel. Semantic coupling with A137: mock reward shaping should match `_compute_progress` expectations — coordinate the `reward` field meaning (level_gain float).

## Risks

- Unknown test dependencies on old mock behavior — budget triage time; the suite is fast (~5s).
- `benchmarks/arc3/` is MCP-seam-exempt (A030) so no import-boundary concerns.
