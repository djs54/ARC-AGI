# Plan: A150 — Weak Predictions Must Falsify, Not Confirm

## Context

`agents/arc4/evaluator.py::evaluate()` computes `effect_match` (lines 78-88 in the current file) via a `satisfies_map` keyed on `predicted_kind`:

```python
satisfies_map = {
    "grid_change": {"grid_change", "level_gain", "state_change"},
    "level_gain": {"level_gain"},
    "state_change": {"state_change"},
    "no_change": {"no_change"},
}
effect_match = observed_kind in satisfies_map.get(str(predicted_kind), set())
```

`grid_change` is the default predicted kind for any `ACTION6` candidate not backed by graph evidence (`plan_generator.py::_predicted_outcome`, lines 473-486, always returns `{"kind": "grid_change", ...}` for untested/no-evidence candidates). Because nearly any click on an ARC frame changes at least one pixel, `observed_kind` is `"grid_change"` almost every step, so `effect_match` is `True` almost every step — even when the click achieved nothing. This routes into the `elif effect_match:` branch (evaluator.py ~line 122) with `reason="prediction_confirmed_without_progress"` and `falsification_delta=0`, never reaching the falsification branch below it.

Live evidence: `artifacts/submission_results_single.live.jsonl` (game `s5i5-18d95033`, 2026-06-25) — 4 steps, 4 different `ACTION6` coordinates, all 4 logged `prediction_confirmed_without_progress`, `falsification_delta=0` throughout, run ended `strategy_exhausted`.

## Implementation Steps

### Step 1: Narrow `effect_match` for weak predictions

In `agents/arc4/evaluator.py`, near the top of the file (module level, alongside other constants):

```python
WEAK_PREDICTION_KINDS = frozenset({"grid_change"})
```

In `evaluate()`, immediately after the existing `effect_match` computation (current lines ~78-88), add:

```python
weak_prediction_override = False
if predicted_kind in WEAK_PREDICTION_KINDS and effect_match and not meaningful_progress:
    effect_match = False
    weak_prediction_override = True
```

Place this **before** the `decision = WorkflowDecision.CONTINUE` block (current line 110) so it affects the `elif effect_match:` vs `else:` branching below unchanged.

### Step 2: Surface the override in metadata

In the `EvaluationResult(...)` construction (current lines 144-173), add to the `metadata` dict:

```python
"weak_prediction_override": weak_prediction_override,
```

This lets telemetry/tests distinguish "confirmed but weak, forced to falsify" from a genuine hard-prediction miss (`predicted_kind="level_gain"` that didn't happen), without inventing a new `reason` string — the existing `reason="prediction_falsified"` / `"repeated_falsification"` paths already apply once `effect_match=False`.

### Step 3: Verify the falsification path fires correctly

No change needed to the `else:` branch (current lines 124-135) — it already handles `effect_match=False` correctly:

```python
else:
    if grid_changed_flag:
        falsification_delta = 0
        reason = "effect_without_progress"
    else:
        falsification_delta = 1
        ...
```

**Important**: note the existing `if grid_changed_flag:` sub-branch at line 125 — when the grid *did* change (which it will, since that's exactly the `grid_change` kind case), `falsification_delta` stays `0` with `reason="effect_without_progress"`. This is a second place the same bug resurfaces one level down: a weak-kind prediction with a real grid change still won't accumulate falsification pressure through this path.

Fix: gate that sub-branch too. Change:

```python
if grid_changed_flag:
    falsification_delta = 0
    reason = "effect_without_progress"
```

to:

```python
if grid_changed_flag and not weak_prediction_override:
    falsification_delta = 0
    reason = "effect_without_progress"
elif weak_prediction_override:
    falsification_delta = 1
    reason = "weak_prediction_falsified"
    projected_count = current_falsification_count + falsification_delta
    if projected_count >= self._limits.repeated_falsification_threshold:
        decision = WorkflowDecision.PIVOT
        reason = "repeated_falsification"
```

else clause (non-grid-changed, non-weak-override) is unchanged from the existing `else:` below it.

### Step 4: Tests

New file `tests/test_a150_weak_prediction_falsification.py`:

1. `test_grid_change_prediction_no_progress_falsifies` — construct an `ExecutionResult` with `candidate.predicted_outcome={"kind": "grid_change", "confidence": 0.3}`, `did_progress=False`, metadata `grid_changed=True`. Assert `evaluation.falsification_delta == 1` and `evaluation.metadata["weak_prediction_override"] is True`.
2. `test_grid_change_prediction_with_progress_still_confirms` — same setup but `did_progress=True` (meaningful_progress). Assert `falsification_delta == 0`, `reason == "meaningful_progress"`, `weak_prediction_override` False (the meaningful-progress branch is checked before this override applies — confirm it short-circuits correctly).
3. `test_level_gain_prediction_unaffected` — `predicted_outcome={"kind": "level_gain", ...}`, no progress, observed `grid_change` only (not level_gain) → this is a genuine mismatch, should already falsify via the *existing* path (`effect_match=False` because `grid_change` not in `satisfies_map["level_gain"]`) — assert behavior unchanged from pre-A150 (regression guard).
4. `test_state_change_prediction_unaffected` — same shape for `state_change` kind.
5. `test_repeated_weak_falsification_reaches_pivot` — call `evaluate()` twice with the same `book_id`/`action_id`, `state.action_falsification_counts` updated between calls (simulate what `cycle_policy.record_evaluation_outcome` does in the orchestrator) to accumulate to `repeated_falsification_threshold=2`. Assert second call returns `decision == WorkflowDecision.PIVOT`, `reason == "repeated_falsification"`.

### Step 5: Mock-mode end-to-end sanity check

Run a mock v2 puzzle where the scripted game (A141 harness) has a click sequence that produces grid churn without progress for 2+ steps:

```bash
PYTHONPATH=. .venv/bin/python run_single_puzzle.py --agent-version=v2 --num-puzzles 1 --max-steps 6
```

Inspect `artifacts/submission_results_single.live.jsonl` for the run and confirm `falsification_delta` is nonzero by step 2 for a repeated-non-progress click sequence (previously this stayed 0 for the whole run — see the Problem section evidence).

## Verify

```bash
.venv/bin/python -m pytest tests/test_a150_weak_prediction_falsification.py -q
.venv/bin/python -m pytest tests/test_a138_structured_falsifiable_predictions.py -q   # regression guard, exact filename may differ — grep first
make test-a
make test-all
```

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/evaluator.py` | `WEAK_PREDICTION_KINDS` constant; `weak_prediction_override` computation; falsification branch gated on it; new metadata field |
| `tests/test_a150_weak_prediction_falsification.py` | New, 5 tests |

## Risks

- Slightly increases falsification pressure on legitimate early-exploration clicks (an untested `ACTION6` candidate's first attempt now falsifies faster if it doesn't help). This is intentional — A131's exponential decay and A136's forced-exploration-after-N already assume falsification is a real signal; today it silently isn't for the majority of click candidates. Mitigated by `repeated_falsification_threshold=2` still requiring two failed attempts before PIVOT, not one.
- Interacts with A138 tests — Step 4 in this plan is a regression guard specifically because those tests must keep passing unchanged.
