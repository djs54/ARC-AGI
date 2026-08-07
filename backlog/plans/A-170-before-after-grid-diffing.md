# Plan: A170 — Surface Before/After Grid Diffs as Structured Causal Evidence

## Context

`WorkflowState` only ever stores `previous_grid_hash` (a hash, not diffable). Every action's effect collapses to a boolean `grid_changed`. Fix: retain the actual previous grid (ephemeral, not serialized), compute a capped structured diff each perception step, and surface it to evaluation and the next plan-phase LLM prompt.

## Implementation

### 1. Ephemeral previous-grid cache on `WorkflowState`

In `agents/arc4/types.py::WorkflowState`, add:

```python
previous_grid: list[list[Any]] | None = field(default=None, repr=False, compare=False)
```

Do **not** add it to `to_dict()`/`from_dict()` — this is a runtime-only cache, not persisted state (keeps serialized artifact/telemetry size unchanged, matches how large ephemeral fields are already excluded elsewhere in this dataclass if any precedent exists — check first).

### 2. Diff computation in `perceive.py`

```python
def _diff_grids(previous: Sequence[Sequence[Any]] | None, current: Sequence[Sequence[Any]], *, max_entries: int = 50) -> dict[str, Any]:
    if previous is None or len(previous) != len(current):
        return {"changed_cells": [], "changed_count": 0, "truncated": False}
    changes = []
    for row_index, (prev_row, cur_row) in enumerate(zip(previous, current)):
        if len(prev_row) != len(cur_row):
            continue
        for col_index, (prev_val, cur_val) in enumerate(zip(prev_row, cur_row)):
            if prev_val != cur_val:
                changes.append({"row": row_index, "col": col_index, "from": prev_val, "to": cur_val})
    truncated = len(changes) > max_entries
    return {
        "changed_cells": changes[:max_entries],
        "changed_count": len(changes),
        "truncated": truncated,
    }
```

In `perceive()`, after computing `normalized_grid`:

```python
grid_diff = self._diff_grids(state.previous_grid, normalized_grid)
snapshot.metadata["grid_diff"] = grid_diff
state.previous_grid = normalized_grid
```

(Order matters: diff against the *old* `state.previous_grid` before overwriting it.)

### 3. Evaluator enrichment

In `evaluator.py::evaluate`, where `actual_effect`/evaluation metadata is built, add the diff summary (e.g. `changed_count`, a short color-transition list) into the metadata dict — additive only, do not touch `observed_kind`'s existing classification branches (`grid_change`/`no_change`/`level_gain`/`state_change`) or any of the override checks (A150, A152, A163) that key off it.

### 4. Thread into `plan_generator.py`'s LLM prompt

Add the most recent `perception.metadata.get("grid_diff")` (when non-empty) to `_query_llm`'s user-message payload, alongside A169's `grid_text`.

## Tests

New `tests/test_a170_grid_diffing.py`:

1. `test_diff_detects_changed_cells` — known before/after grid pair → exact expected `changed_cells` list.
2. `test_diff_no_previous_grid_returns_empty` — first perception of a run, no error, empty diff.
3. `test_diff_truncates_large_changesets` — a diff exceeding `max_entries` → `truncated: True`, count still accurate, list capped.
4. `test_diff_shape_mismatch_returns_empty` — previous/current grids of different shapes (e.g. after a `RESET`) → empty diff, no crash.
5. `test_evaluator_actual_effect_enriched_without_changing_observed_kind` — construct an evaluation scenario with a known diff, assert `observed_kind` matches what it would have been pre-A170 (regression guard) while the new diff-derived metadata is also present.
6. Re-run `tests/test_a135_graph_driven_planning.py`/`tests/test_a163_fetch_causal_path_field_mismatch.py` unchanged — confirm A163's causal-override logic is unaffected.

## Verify

```bash
.venv/bin/python -m pytest tests/test_a170_grid_diffing.py tests/test_a135_graph_driven_planning.py -v
make test-a
make test-all
```

Live confirmation: find a live-smoke step where an action actually changed the grid, confirm `grid_diff.changed_count > 0` with plausible `{row, col, from, to}` entries, and confirm the *next* plan-phase LLM prompt includes that diff.

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/types.py` | `WorkflowState.previous_grid` (ephemeral, unserialized) |
| `agents/arc4/perceive.py` | New `_diff_grids` helper; attached to `snapshot.metadata["grid_diff"]`; updates `state.previous_grid` |
| `agents/arc4/evaluator.py` | Enriches evaluation metadata with the diff, without altering `observed_kind` |
| `agents/arc4/plan_generator.py` | `_query_llm`'s prompt includes the most recent diff |
| `tests/test_a170_grid_diffing.py` | New, 6 tests |

## Risks

- Must not change `observed_kind`'s classification — that would silently alter A150/A152/A163's override behavior, all already live-verified working. Tests explicitly guard this.
- Diffing on every step adds O(rows×cols) work per perception — negligible for ARC-sized grids (≤64x64), no meaningful performance risk.
