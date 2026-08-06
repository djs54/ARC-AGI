# Plan: A138 — Structured Falsifiable Predictions

## Context

Today's prediction pipeline:

1. `agents/arc4/plan_generator.py` `_build_candidates` sets `expected_effect=self._expected_effect(graph_record, goal, action_id)` (line ~181) — a template string like `"advance blob-3 with ACTION1"`.
2. `agents/arc4/executor.py` copies it onto `ExecutionResult.predicted_effect` and sets `actual_effect` from the transport result (the game state string, e.g. `"NOT_FINISHED"`).
3. `agents/arc4/evaluator.py` lines 46-48 string-compares them. They never match.

Relevant types (`agents/arc4/types.py`): `PlanCandidate` (search `class PlanCandidate`) has `action_id`, `expected_effect`, `score`, `rationale`, `metadata`, plus `to_dict`/`from_dict` used by Temporal serialization. `ExecutionResult` (line ~239) has `predicted_effect: str | None`, `actual_effect: str | None`, `metadata`.

A137 (prerequisite) makes `execution.metadata` carry `grid_changed: bool` and `level_gain: int`.

## Implementation Steps

### Step 1: Add `predicted_outcome` to PlanCandidate

**File:** `agents/arc4/types.py`

Add field `predicted_outcome: dict[str, Any] = field(default_factory=dict)` to `PlanCandidate`. Include it in `to_dict()` and `from_dict()` (default `{}` when absent so old serialized payloads still load). Outcome schema (document in a docstring on the field or class):

```python
# {"kind": "grid_change" | "no_change" | "level_gain" | "state_change", "confidence": float}
```

### Step 2: Planner emits structured predictions

**File:** `agents/arc4/plan_generator.py`

Add a static method next to `_expected_effect`:

```python
@staticmethod
def _predicted_outcome(graph_record: Mapping[str, Any], graph_evidence: Mapping[str, Any], is_untested: bool) -> dict[str, Any]:
    # Graph evidence with supports → predict the recorded effect kind
    raw = (graph_evidence or {}).get("raw") or {}
    recorded_kind = raw.get("effect_kind") or graph_record.get("effect_kind")
    if recorded_kind in ("grid_change", "no_change", "level_gain", "state_change"):
        confidence = float((graph_evidence or {}).get("confidence") or 0.5)
        return {"kind": recorded_kind, "confidence": confidence}
    if is_untested:
        return {"kind": "grid_change", "confidence": 0.3}  # weak default: "does something"
    return {"kind": "grid_change", "confidence": 0.4}
```

In `_build_candidates`, where `_CandidateRecord` is constructed (~line 173), the record's metadata already holds `graph_evidence` and `untested`. Thread `predicted_outcome` through `_CandidateRecord` (add field to the dataclass at top of file) and into `_to_plan_candidate` so the final `PlanCandidate.predicted_outcome` is populated. Keep `expected_effect` as the human-readable rendering: `f"{action_id}: expect {outcome['kind']} (p={outcome['confidence']:.2f})"`.

### Step 3: Executor passes it through

**File:** `agents/arc4/executor.py`

`ExecutionResult` already carries `candidate=plan` (the full PlanCandidate), so the evaluator can read `execution.candidate.predicted_outcome`. No schema change needed — verify `_success` keeps `candidate=plan` (line ~75) and that Temporal activity serialization round-trips it (it serializes via `PlanCandidate.to_dict`, covered by Step 1).

### Step 4: Evaluator does structured comparison

**File:** `agents/arc4/evaluator.py`, method `evaluate`

Replace the string `effect_match` (lines 46-48) with:

```python
predicted = (execution.candidate.predicted_outcome if execution.candidate else None) or {}
exec_meta = execution.metadata if isinstance(execution.metadata, Mapping) else {}
observed_kind = (
    "level_gain" if int(exec_meta.get("level_gain") or 0) > 0
    else "state_change" if str(exec_meta.get("state") or "") in ("WIN", "GAME_OVER")
    else "grid_change" if exec_meta.get("grid_changed")
    else "no_change"
)
predicted_kind = predicted.get("kind")
# level_gain implies grid_change: a stronger outcome satisfies a weaker prediction
_SATISFIES = {"grid_change": {"grid_change", "level_gain", "state_change"},
              "level_gain": {"level_gain"},
              "state_change": {"state_change"},
              "no_change": {"no_change"}}
effect_match = predicted_kind is not None and observed_kind in _SATISFIES.get(predicted_kind, set())
```

Keep the legacy string comparison ONLY as a fallback when `predicted` is empty (old transports/tests): retain the existing lines guarded by `if not predicted:`.

In the decision block, the existing branch `elif effect_match:` (reason `prediction_confirmed_without_progress`) now actually fires. Add `observed_kind` and `predicted_kind` to evaluation metadata.

### Step 5: Feed confirmations to the graph

**File:** `agents/arc4/evaluator.py`, `_record_evaluation` and **`agents/arc4/graph_queries.py`** (find `record_evaluation` implementation).

Ensure the recorded payload includes `effect_match` and `observed_kind` so `arc_record_action_effect` / `arc_confirm_hypothesis` receive confirmations, not only contradictions. Check how `graph_queries.py` maps evaluation → MCP tool calls; add `observed_kind` to the recorded effect so future `_predicted_outcome` calls can read `effect_kind` back from evidence (closing the loop with Step 2).

### Step 6: Tests

**File:** `tests/test_a138_structured_predictions.py` (new)

1. `test_plan_candidate_serializes_predicted_outcome` — to_dict/from_dict round-trip
2. `test_planner_untested_action_predicts_grid_change`
3. `test_planner_uses_graph_effect_kind_when_present`
4. `test_evaluator_confirms_grid_change_prediction` — predicted grid_change, exec_meta grid_changed=True → effect_match True, reason `prediction_confirmed_without_progress`, falsification_delta 0
5. `test_evaluator_falsifies_grid_change_prediction_on_no_change` — grid_changed=False → effect_match False
6. `test_level_gain_satisfies_grid_change_prediction`
7. `test_legacy_string_fallback_when_no_structured_prediction` — empty predicted_outcome → old string path still works

### Step 7: Verify

```bash
make test-a
.venv/bin/python -m pytest tests/test_a138_structured_predictions.py tests/test_arc4_evaluator.py tests/test_a137_graded_progress_signal.py -q
```

Then one live smoke; confirm the artifact shows nonzero `effect_match=true` steps:

```bash
CAMPY_MCP_CMD="../hippocampy/.venv/bin/python -m campy.adapters.mcp_server" ARC_TEMPORAL_ENABLED=1 PYTHONPATH=. .venv/bin/python run_single_puzzle.py --agent-version=v2 --num-puzzles 1 --max-steps 50 --temporal --live-smoke
```

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/types.py` | `PlanCandidate.predicted_outcome` + serialization |
| `agents/arc4/plan_generator.py` | `_predicted_outcome`, thread through candidates |
| `agents/arc4/evaluator.py` | Structured effect_match; metadata; record observed_kind |
| `agents/arc4/graph_queries.py` | Include observed_kind/effect_match in recorded effects |
| `tests/test_a138_structured_predictions.py` | New, 7 tests |

## Conflict Note (for fan-out)

Touches `evaluator.py` (conflicts with A137 — land A137 first) and `plan_generator.py` (conflicts with A139 — coordinate or land sequentially).

## Risks

- Old serialized Temporal payloads lack `predicted_outcome` → from_dict default `{}` + legacy fallback path covers this.
- Graph schema may not have an `effect_kind` field yet; recording it as part of the effect payload is additive and safe (MCP tools accept extra metadata). If `arc_record_action_effect` rejects unknown fields, put it inside the existing free-form metadata/reasoning blob.
