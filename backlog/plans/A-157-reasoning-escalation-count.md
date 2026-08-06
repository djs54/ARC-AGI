# Plan: A157 — `reasoning_escalation_count` Is Hardcoded to Zero

## Context

`agents/arc4/telemetry.py::_step_snapshot` (current lines 133-200) builds the per-step live.jsonl row. Lines 160-167 build the base snapshot dict, including two literal-hardcoded stub fields:

```python
"reasoning_skip_count": 0,
"reasoning_escalation_count": 0,
```

`goal = self._latest_goal` (current line 136) is already in scope — a `ResolvedGoal` whose `.metadata` dict carries `"llm_escalated": bool` (set in `agents/arc4/goal_resolver.py::resolve()`, current line 65, `True` when `_should_escalate_to_llm` triggers and the LLM call returns a parseable patch).

Live evidence: game `ar25-0c556536`, 2026-08-05 — the goal-ambiguity LLM prompt fired on all 10 steps (confirmed via `grep -c "Resolve the ARC goal ambiguity" <log>` → 10), but every step's `reasoning_escalation_count` in the live.jsonl artifact was `0`.

## Implementation Steps

### Step 1: Derive `reasoning_escalation_count` from the real signal

Change (current line 167):

```python
"reasoning_escalation_count": 0,
```

to:

```python
"reasoning_escalation_count": int(bool(goal.metadata.get("llm_escalated"))) if goal is not None else 0,
```

Leave `"reasoning_skip_count": 0,` (current line 166) unchanged — no confirmed real source for it yet (see card Notes).

### Step 2: Tests

New file `tests/test_a157_reasoning_escalation_count.py`. Follow the `ArcV2Telemetry` direct-construction pattern established in `tests/test_a156_step_failure_class_overload.py` (construct `ArcV2Telemetry(...)`, set `_latest_evaluation`/`_latest_goal` directly, call `_step_snapshot(())`).

1. `test_llm_escalated_true_reports_escalation_count_one` — `telemetry._latest_goal = ResolvedGoal(selected=..., alternatives=(), grounding_gate_passed=True, metadata={"llm_escalated": True})`, plus a minimal `_latest_evaluation` (needed for `_step_snapshot` to run its full body without erroring — check whether `evaluation` being `None` short-circuits before reaching the `reasoning_escalation_count` key or not; if the field is unconditionally in the base `snapshot = {...}` dict rather than the `if evaluation is not None:` update block, `_latest_evaluation` can stay `None` — confirm by reading the method, current lines 153-188, before deciding whether the test fixture needs an evaluation object at all). Assert `snapshot["reasoning_escalation_count"] == 1`.
2. `test_llm_escalated_false_reports_escalation_count_zero` — same shape with `metadata={"llm_escalated": False}` (or `{}`, missing key) — assert `snapshot["reasoning_escalation_count"] == 0`.
3. `test_no_goal_reports_escalation_count_zero` — `telemetry._latest_goal = None` — assert `snapshot["reasoning_escalation_count"] == 0`, no crash.

## Verify

```bash
.venv/bin/python -m pytest tests/test_a157_reasoning_escalation_count.py -v
make test-a
make test-all
```

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/telemetry.py` | `reasoning_escalation_count` derived from `goal.metadata.get("llm_escalated")` instead of hardcoded `0` |
| `tests/test_a157_reasoning_escalation_count.py` | New, 3 tests |

## Risks

- Minimal — single-field, subtractive-to-additive change (replacing a constant with a real read), no new logic surface, no behavior change outside telemetry output.
