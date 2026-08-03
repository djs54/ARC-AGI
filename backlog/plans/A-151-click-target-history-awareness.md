# Plan: A151 — Click Targets Must Avoid Already-Attempted Coordinates

## Context

`agents/arc4/plan_generator.py::_build_candidates()` calls `_click_targets(perception, limit=self._limits.click_target_limit)` (current line ~148) to get up to 3 `(x, y)` targets for `ACTION6`, then builds one candidate per target keyed as `book_id = f"ACTION6@{x},{y}"` (lines 150-157). `state.action_attempt_counts` is already keyed by these exact `book_id` strings and is in scope at the call site (`_build_candidates` receives `state` as its first parameter) — but `_click_targets` itself is a `@staticmethod` with no access to it, so target generation is blind to history.

Evidence of the bug: `artifacts/submission_results_single.live.jsonl`, game `s5i5-18d95033` — step 1 clicks `(52,10)`, step 4 clicks `(52,10)` again (see `solve_phase_summary.action_attempt_counts: {"ACTION6@52,10": 2, ...}` in the final_result row).

## Implementation Steps

### Step 1: Thread attempted coordinates into `_click_targets`

Change the signature (current lines 488-489):

```python
@staticmethod
def _click_targets(perception: PerceptionSnapshot, limit: int = 3) -> list[dict[str, Any]]:
```

to:

```python
@staticmethod
def _click_targets(
    perception: PerceptionSnapshot,
    limit: int = 3,
    *,
    attempted_coords: Mapping[tuple[int, int], int] | None = None,
) -> list[dict[str, Any]]:
```

`attempted_coords` maps `(x, y) -> attempt_count`, built once per `_build_candidates` call (not per-target) to avoid re-parsing `state.action_attempt_counts` for every entity.

### Step 2: Build the attempted-coords map at the call site

In `_build_candidates` (current lines 110-126), before the `for action_id in available_actions[...]:` loop, add:

```python
attempted_click_coords: dict[tuple[int, int], int] = {}
for book_id, count in state.action_attempt_counts.items():
    if book_id.startswith("ACTION6@") and count > 0:
        coord_part = book_id[len("ACTION6@"):]
        try:
            x_str, y_str = coord_part.split(",", 1)
            attempted_click_coords[(int(x_str), int(y_str))] = count
        except ValueError:
            continue
```

Then update the call at the current `if action_id == "ACTION6":` block (line ~147-148):

```python
click_targets = self._click_targets(perception, limit=self._limits.click_target_limit, attempted_coords=attempted_click_coords)
```

### Step 3: Penalize/exclude repeats inside `_click_targets`

In the scoring loop inside `_click_targets` (current lines 496-525), after computing `salience` and before appending to `scored`:

```python
attempts_here = (attempted_coords or {}).get((x, y), 0)
if attempts_here:
    salience -= 0.5 * attempts_here  # push repeats behind fresh targets; does not floor at 0 so relative ranking among repeats is preserved
```

`0.5` is chosen to exceed the largest realistic salience delta between two fresh candidates (salience is built from bounded terms: `1/(1+cell_count)` ≤ 1, `rarity` ≤ 1, `+0.2` kind bonus — max ~2.2), so a single prior attempt reliably drops a target below any untried one, while still preserving a full ranking (not a hard exclusion) so the all-attempted fallback in Step 4 works.

### Step 4: Guard against an empty result when everything is attempted

`_click_targets` already returns `scored[:limit]` after sorting descending by salience (current line 527-528) — since Step 3 uses a penalty rather than a hard filter, the list is never emptied by history alone; a heavily-attempted target simply sorts last. No separate fallback branch is needed here. Confirm this in the tests (Step 5, case 3) rather than adding new code — if the test shows candidates keep including some negative-salience repeats crowding out a low-salience-but-fresh target, tighten the penalty coefficient instead of adding exclusion logic.

### Step 5: Tests

New file `tests/test_a151_click_target_history.py`:

1. `test_fresh_coordinate_ranks_first_among_equals` — two entities with identical salience inputs, no attempted_coords → order is deterministic (existing tie-break behavior), sanity baseline.
2. `test_exact_repeat_ranks_below_fresh_alternative` — two entities, one at an already-attempted coordinate (`attempted_coords={(x1,y1): 1}`), one fresh, otherwise equal salience → fresh one ranks first.
3. `test_all_attempted_still_returns_candidates` — all perceived entities' coordinates present in `attempted_coords` → `_click_targets` still returns up to `limit` targets (not empty), ordered by fewest attempts first among equals.
4. `test_unrelated_entities_unaffected` — entity at a coordinate not in `attempted_coords` → salience unchanged vs. calling without the parameter at all.
5. `test_build_candidates_threads_state_attempts` — integration-level: call `_build_candidates` with a `WorkflowState` whose `action_attempt_counts` includes `"ACTION6@52,10": 2`, perception with an entity centroid mapping to `(52, 10)` and another to `(10, 20)`; assert the `(10,20)` candidate outranks `(52,10)` in the returned candidate list ordering (post `_rank_candidates`).

### Step 6: Mock/live sanity

```bash
PYTHONPATH=. .venv/bin/python run_single_puzzle.py --agent-version=v2 --num-puzzles 1 --max-steps 8
```

Confirm no `ACTION6@x,y` book_id in the resulting `solve_phase_summary.action_attempt_counts` exceeds what's expected given available fresh targets in the mock scripted game (A141 harness) — i.e., the planner doesn't revisit a coordinate while untried ones exist.

## Verify

```bash
.venv/bin/python -m pytest tests/test_a151_click_target_history.py -q
.venv/bin/python -m pytest tests/test_a139_action6_coordinate_targeting.py -q   # regression guard, grep exact filename first
make test-a
make test-all
```

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/plan_generator.py` | `_click_targets` gains `attempted_coords` param + salience penalty; `_build_candidates` builds the coords map from `state.action_attempt_counts` and passes it through |
| `tests/test_a151_click_target_history.py` | New, 5 tests |

## Risks

- If an entity's true position shifts by exactly 0 pixels between frames despite the puzzle expecting repeated clicks at the same spot to matter (some ARC games use repeated identical clicks as a valid strategy — e.g. a counter or toggle), a strong penalty could suppress a legitimately correct repeated action. Mitigated by using a penalty (not exclusion) so the coordinate can still be selected if no better alternative exists, and by A150 separately making the falsification signal do the real work of learning "this doesn't help" — A151 only biases exploration, it doesn't forbid revisits.
