# Plan for B163 — Reduce ArchetypeClassifier MIN_OBSERVATIONS from 5 to 2

## Card Metadata
- **Card**: B163
- **Priority**: P1
- **Dependencies**: None

## Summary

The archetype classifier wastes 5 steps returning `(UNKNOWN, 0.0)` before it starts classifying. On a 15-step budget, this is 33% of the run with no strategic direction. Lower the gate to 2 observations and add a fast-track path for strong initial signals.

## Technical Approach

### Step 1: Lower constants

In `agents/arc3/solver.py`, `ArchetypeClassifier`:

```python
MIN_OBSERVATIONS: int = 2    # was 5
LOCK_THRESHOLD: float = 0.55  # was 0.65
```

### Step 2: Fast-track classification

Add a method `fast_track_classify(grid_summary: dict) -> Optional[tuple[GameArchetype, float]]`:

- Input: the grid analysis summary from B162 (or any dict with `n_regions`, `distinct_colors`, `region_sizes`)
- Logic:
  - If `n_regions >= 3` and any region has < 20 pixels (small object) → hint `SPACE` with confidence 0.4
  - If exactly 2 non-background regions and one is elongated (aspect ratio > 3:1) → hint `RACE` with confidence 0.35
  - If `n_regions >= 5` with mixed sizes → hint `SPACE` with confidence 0.3
- Called once at bootstrap (step 0), before `update()` starts counting observations
- If fast-track returns a result, set `_consecutive_best` and `_consecutive_count = 1` so the first `update()` call starts from a non-zero baseline

### Step 3: Update tests

- Update `test_arc3_solver.py` tests that assert on `MIN_OBSERVATIONS = 5` behavior
- Add new test: `test_fast_track_classify_with_strong_signals`
- Add new test: `test_archetype_available_by_step_2`

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/solver.py` | Change constants, add `fast_track_classify()` method |
| `tests/test_arc3_solver.py` | Update existing MIN_OBSERVATIONS tests |
| `tests/test_b163_faster_classification.py` | New: fast-track tests, step-2 availability test |

## Acceptance Criteria

1. `ArchetypeClassifier.MIN_OBSERVATIONS == 2`
2. `ArchetypeClassifier.LOCK_THRESHOLD == 0.55`
3. `fast_track_classify()` returns a non-UNKNOWN archetype when grid has ≥3 distinct regions with a small object
4. After 2 observations with consistent signals, archetype confidence > 0
5. `pytest tests/test_arc3_solver.py tests/test_b163_*.py -q` all pass

## Validation Commands

```bash
pytest tests/test_b163_faster_classification.py -v
pytest tests/test_arc3_solver.py -q
```

## Risks / Constraints

- Lowering MIN_OBSERVATIONS could cause premature locking on the wrong archetype. Mitigated by: (a) the dissonance detector can still reset, (b) lock threshold is still > 0.5 so weak signals don't lock.
- Fast-track heuristics are deliberately conservative (max confidence 0.4) to avoid overcommitting.
