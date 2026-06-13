# Plan: A139 — ACTION6 Coordinate Targeting From Perceived Entities

## Context

Data already available at plan time: `PerceptionSnapshot.entities` is a tuple of `PerceivedEntity` (see `agents/arc4/types.py` and `agents/arc4/perceive.py` `_component_to_entity`). Each entity has `kind` ("point"/"line"/"block"/"blob"), `value` (color as string), and `attributes` including `centroid: (row, col)`, `bbox`, `cell_count`, `coverage`.

Important coordinate convention: perception centroids are `(row, col)`; the ARC API wants `x` (column) and `y` (row), both in [0, 63]. **x = col, y = row.**

Execution path: `PlanCandidate` → vet → `agents/arc4/executor.py` calls `self.transport(plan.action_id, action_args, context)` — find how `action_args` is built (search `action_args` in executor.py); it must include the candidate payload. `run_single_puzzle.py` `execute_action` then does `request_payload.update(dict(payload))`, so x/y flow to the API once they're in the payload.

Bookkeeping today: `state.action_attempt_counts` / `action_falsification_counts` key on bare `action_id`. Evaluator `_action_family` (`agents/arc4/evaluator.py` line ~152) splits on `:`, `-`, `_` — note `-` would wrongly split `ACTION6@10,20` only if we used `-`; we use `@` which is NOT in the separator list, so add `@` handling explicitly.

## Implementation Steps

### Step 1: `PlanCandidate.payload`

**File:** `agents/arc4/types.py`

Add `payload: dict[str, Any] = field(default_factory=dict)` to `PlanCandidate`; include in `to_dict`/`from_dict` (default `{}`). This is the action-argument dict, distinct from `metadata`.

### Step 2: Target selection heuristic

**File:** `agents/arc4/plan_generator.py` (new static method)

```python
@staticmethod
def _click_targets(perception: PerceptionSnapshot, limit: int = 3) -> list[dict[str, Any]]:
    """Rank perceived entities as click targets: small distinct objects first, background last."""
    scored = []
    for entity in perception.entities:
        attrs = entity.attributes or {}
        coverage = float(attrs.get("coverage") or 0.0)
        if coverage > 0.5:           # background-sized blob — skip
            continue
        cell_count = int(attrs.get("cell_count") or 0)
        if cell_count == 0:
            continue
        centroid = attrs.get("centroid") or (0, 0)
        row, col = int(round(float(centroid[0]))), int(round(float(centroid[1])))
        x = max(0, min(63, col))
        y = max(0, min(63, row))
        # salience: prefer small, compact, distinct objects
        salience = 1.0 / (1.0 + cell_count) + (0.2 if entity.kind in ("point", "block") else 0.0)
        scored.append((salience, {"x": x, "y": y, "entity_kind": entity.kind, "entity_color": entity.value}))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [target for _, target in scored[:limit]]
```

### Step 3: Expand ACTION6 into targeted candidates

**File:** `agents/arc4/plan_generator.py`, `_build_candidates`

In the loop over `available_actions`, when `action_id == "ACTION6"`, instead of one candidate, emit one `_CandidateRecord` per target from `_click_targets(perception)` (fall back to a single (32,32) center click if no entities). For each targeted candidate:

- composite id for bookkeeping: `book_id = f"ACTION6@{x},{y}"` — use `book_id` for `state.action_attempt_counts` / `action_falsification_counts` lookups (attempts, falsifications, untested bonus)
- `action_id` stays `"ACTION6"` (the executor/transport posts `/api/cmd/ACTION6`)
- `payload = {"x": x, "y": y}`
- rationale: `f"click {entity_kind} color={entity_color} at ({x},{y})"`
- metadata: include `book_id` and target info

`_CandidateRecord` needs `payload` and `book_id` fields; `_to_plan_candidate` copies `payload` onto `PlanCandidate.payload` and `book_id` into metadata.

### Step 4: Bookkeeping uses book_id

**Files:** `agents/arc4/workflow.py` and `agents/arc4/temporal_workflows.py`

`_record_execution_attempt` / `_record_evaluation_state` (workflow.py lines ~184-193) and the equivalent dict-based logic in temporal_workflows.py (lines ~140-151) key on `execution.action_id`. Change to prefer the candidate's `book_id`: `key = (execution.candidate.metadata or {}).get("book_id") or execution.action_id` (workflow) and the serialized-dict equivalent (temporal). If A140 (unified cycle policy) has landed, make this change once in the shared module instead.

**File:** `agents/arc4/evaluator.py` `_action_family` (line ~152): add `@` as the first separator checked, so the family of `ACTION6@10,20` is `ACTION6`.

### Step 5: Executor payload passthrough

**File:** `agents/arc4/executor.py`

Where `action_args` is constructed for the transport call, merge `plan.payload`. Verify `run_single_puzzle.py` `execute_action` receives x/y in `payload` (it already does `request_payload.update(dict(payload))`; the `setdefault("x", ...)` lines then become the no-entity fallback).

### Step 6: Telemetry

**File:** `agents/arc4/telemetry.py`

In step-row construction, when the executed candidate has payload x/y, emit `action_x`, `action_y`.

### Step 7: Tests

**File:** `tests/test_a139_action6_targeting.py` (new)

1. `test_click_targets_prefers_small_entities` — two entities (cell_count 4 vs 400) → small one ranked first
2. `test_click_targets_skips_background` — coverage 0.8 entity excluded
3. `test_click_targets_clamps_coordinates` — centroid (70.0, -3.0) → y=63, x=0
4. `test_click_targets_xy_orientation` — centroid (row=10, col=40) → x=40, y=10  ← guards the transpose bug
5. `test_planner_expands_action6_per_target` — perception with 2 entities + ACTION6 available → 2 targeted candidates with distinct payloads
6. `test_action6_fallback_center_click_without_entities`
7. `test_book_id_separates_attempt_counts` — falsify ACTION6@5,5 → ACTION6@20,20 still gets untested bonus
8. `test_action_family_of_composite_id` — `_action_family("ACTION6@10,20") == "ACTION6"`

### Step 8: Verify

```bash
make test-a
.venv/bin/python -m pytest tests/test_a139_action6_targeting.py tests/test_a135_graph_driven_planning.py tests/test_a136_mechanic_prior_extraction.py -q
```

Live smoke on a game with ACTION6 available; confirm step rows show varied `action_x/action_y`.

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/types.py` | `PlanCandidate.payload` |
| `agents/arc4/plan_generator.py` | `_click_targets`, ACTION6 expansion, book_id |
| `agents/arc4/workflow.py` + `temporal_workflows.py` | attempt/falsification keyed by book_id |
| `agents/arc4/evaluator.py` | `@` separator in `_action_family` |
| `agents/arc4/executor.py` | merge plan.payload into action args |
| `agents/arc4/telemetry.py` | action_x/action_y |
| `tests/test_a139_action6_targeting.py` | New, 8 tests |

## Conflict Note (for fan-out)

Touches `plan_generator.py` (conflicts with A138), `workflow.py`/`temporal_workflows.py` (conflicts with A140), `types.py` (conflicts with A138's PlanCandidate change — trivial merge). Sequence after A138 or coordinate field additions.

## Risks

- x/y orientation mistake is the classic bug here — test 4 exists specifically for it.
- max_candidates (6) may be consumed by click targets crowding out simple actions; cap targets at 3 and consider raising `PlanGeneratorLimits.max_candidates` to 8.
