# Plan: A152 — Goal Confidence Must Reflect Repeated Failure

## Context

`agents/arc4/goal_resolver.py::resolve()` runs every workflow cycle and recomputes hypotheses from scratch via `_tier_one_hypotheses()` (perception-only, current lines 77-128), optionally merges graph evidence (`_merge_graph_evidence`, lines 130-147), optionally escalates to an LLM (`_should_escalate_to_llm` / `_query_llm`, lines 189-225), then applies `_apply_grounding_gate` (lines 267-297) which only clamps confidence **upward drift** on a stalled goal — it never demotes.

`WorkflowState` (`agents/arc4/types.py`, lines 349-403) has `action_attempt_counts` / `action_falsification_counts` for actions but nothing equivalent for goals. `state.active_goal` is overwritten every cycle (`workflow.py:60,93`), so there is no carried memory of "this goal has failed N times in a row."

Live evidence: `artifacts/submission_results_single.live.jsonl`, game `s5i5-18d95033` — `active_goal_hypothesis_id="block-5"`, `active_goal_confidence=0.57` unchanged across all 4 logged steps despite 4/4 no-progress evaluations.

## Implementation Steps

### Step 1: Add goal failure tracking to `WorkflowState`

In `agents/arc4/types.py`, add a field to `WorkflowState` (alongside `action_falsification_counts`, current line 360):

```python
goal_failure_counts: dict[str, int] = field(default_factory=dict)
```

Update `to_dict()` (lines 366-383) and `from_dict()` (lines 385-403) to include it, matching the existing `action_falsification_counts` pattern exactly (plain dict, no nested serialization needed).

### Step 2: Increment/reset the counter in both orchestrators

In `agents/arc4/workflow.py`, find `_record_evaluation_state` (referenced at line 143) — read its current body first (it's the method that already updates `action_falsification_counts`/`consecutive_no_progress_count` from `evaluation_payload`). Add, in the same method:

```python
goal_id = resolved_goal_payload.selected.goal_id  # or however the active goal_id is accessible in this scope — confirm exact attribute path by reading ResolvedGoal in types.py
if evaluation_payload.meaningful_progress:
    state.goal_failure_counts[goal_id] = 0
else:
    state.goal_failure_counts[goal_id] = state.goal_failure_counts.get(goal_id, 0) + 1
```

Mirror the identical logic in `agents/arc4/temporal_workflows.py`'s equivalent inline state-update block (grep for where `action_falsification_counts` is mutated in that file — same pattern, likely inside the sandboxed workflow run method rather than a separate helper, since A140 unified policy but this bookkeeping is orchestrator-local state mutation, not pure `cycle_policy.py` logic).

**Do not** put this in `cycle_policy.py` unless it can be expressed as a pure function taking/returning plain values like the existing helpers there — if it needs `ResolvedGoal`/`EvaluationResult` objects directly, keep it inline in each orchestrator like the surrounding bookkeeping already is, to avoid introducing non-Temporal-safe imports into the shared pure module.

### Step 3: Apply failure-based decay in `goal_resolver.py`

Add fields to `GoalResolverLimits` (current lines 14-22):

```python
goal_failure_threshold: int = 2
goal_failure_decay_factor: float = 0.7
```

In `resolve()` (current lines 30-75), after `hypotheses = self._order_hypotheses(hypotheses)` (line 53) and before `_apply_grounding_gate` (line 54), insert a new step:

```python
hypotheses = self._apply_failure_decay(state, hypotheses)
hypotheses = self._order_hypotheses(hypotheses)  # re-sort after decay changes ranking
```

New method:

```python
def _apply_failure_decay(self, state: WorkflowState, hypotheses: list[GoalHypothesis]) -> list[GoalHypothesis]:
    decayed: list[GoalHypothesis] = []
    for hypothesis in hypotheses:
        failures = state.goal_failure_counts.get(hypothesis.goal_id, 0)
        if failures < self._limits.goal_failure_threshold:
            decayed.append(hypothesis)
            continue
        decay = self._limits.goal_failure_decay_factor ** (failures - self._limits.goal_failure_threshold + 1)
        decayed.append(
            GoalHypothesis(
                goal_id=hypothesis.goal_id,
                description=hypothesis.description,
                confidence=hypothesis.confidence * decay,
                evidence=hypothesis.evidence,
                metadata=self._merge_metadata(hypothesis.metadata, {"failure_decay_applied": True, "failure_count": failures}),
            )
        )
    return decayed
```

Note: `resolve()` needs `state` passed to it already (confirm — it's the first parameter per the signature at line 30, so this is already available; no call-site change needed for `resolve()` itself, only for the two orchestrators calling it, which already pass `state`).

### Step 4: Verify grounding-gate interaction order

`_apply_grounding_gate` runs *after* the new decay step (unchanged position). Read its logic (lines 267-297) again once Step 3 lands: it clamps hypotheses whose confidence exceeds `state.active_goal.selected.confidence` (the *previous* cycle's selected confidence) down to that ceiling, when no progress was observed. Since the decayed active goal's own confidence is now lower each cycle, the ceiling itself shrinks over time too — confirm this doesn't create a runaway death-spiral where the ceiling clamps everything (including a fresh alternative) down to a near-zero floor. If `_observed_progress` returns `False` (no grid hash change) *and* decay has already dropped the active goal low, the clamp could suppress a legitimately better alternative. Add a carve-out: only apply the grounding-gate clamp to hypotheses whose `goal_id == state.active_goal.selected.goal_id` (i.e., don't clamp fresh alternatives, only prevent the *same* goal's confidence from inflating without progress) — check whether this carve-out is already implicit or needs an explicit `if hypothesis.goal_id == state.active_goal.selected.goal_id` condition added to the loop at lines 283-296.

### Step 5: Investigate the LLM-escalation question

`_should_escalate_to_llm` (lines 189-198) triggers when `state.consecutive_no_progress_count >= llm_patience_steps=2` (for the single-hypothesis case) or when ambiguous/under-confident (multi-hypothesis case). Check the live run's `agent_execution_trace.json` / the orchestrator's `llm_port` wiring for that smoke invocation — was `llm_port` actually configured (non-`None`)? If it was `None`, `_should_escalate_to_llm` is checked but `llm_port is not None and ...` short-circuits, so no LLM call ever happens regardless of patience — document this as the likely explanation in the plan's findings, not a bug. If `llm_port` *was* configured and it still re-selected `block-5`, that's a separate LLM-prompting issue out of scope for this card — note it for a follow-up card instead of fixing here.

### Step 6: Tests

New file `tests/test_a152_goal_failure_demotion.py`:

1. `test_failure_count_increments_on_no_progress` — call the orchestrator's evaluation-recording step (or test `goal_failure_counts` mutation directly if extracted to a small helper) twice with `meaningful_progress=False`, same `goal_id` → count reaches 2.
2. `test_failure_count_resets_on_progress` — count at 2, then one call with `meaningful_progress=True` → count back to 0.
3. `test_decay_formula_below_threshold_noop` — `goal_failure_counts={"g1": 1}`, threshold=2 → hypothesis confidence unchanged.
4. `test_decay_formula_above_threshold_reduces_confidence` — `goal_failure_counts={"g1": 3}` → confidence multiplied by `decay_factor ** 2`, matches formula exactly.
5. `test_goal_switches_after_repeated_failure` — two hypotheses, `g1` (active, confidence 0.57, failures=3) and `g2` (alternative, confidence 0.5, failures=0) → after decay, `g2.confidence > g1.confidence`, `resolve()` selects `g2`.
6. `test_grounding_gate_does_not_suppress_fresh_alternative` — regression guard for the Step 4 concern: active goal has failed and decayed, a genuinely fresh alternative hypothesis with higher confidence is present → alternative is NOT clamped down by the grounding gate, selection can switch to it.

## Verify

```bash
.venv/bin/python -m pytest tests/test_a152_goal_failure_demotion.py -q
.venv/bin/python -m pytest tests/test_a133_evaluator_progress_detection.py -q   # regression guard on grounding-gate-adjacent behavior, grep exact filename first
make test-a
make test-all
```

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/types.py` | `WorkflowState.goal_failure_counts` field + serialization |
| `agents/arc4/workflow.py` | Increment/reset `goal_failure_counts` in evaluation-recording step |
| `agents/arc4/temporal_workflows.py` | Same, mirrored |
| `agents/arc4/goal_resolver.py` | `GoalResolverLimits` new fields; `_apply_failure_decay` method; grounding-gate carve-out if needed per Step 4 |
| `tests/test_a152_goal_failure_demotion.py` | New, 6 tests |

## Risks

- Highest-complexity card of the four from this investigation — the grounding-gate interaction (Step 4) is subtle and must be tested explicitly (test 6), not assumed safe.
- If `llm_port` turns out to be unconfigured in the live-smoke path generally (Step 5), that's a separate, possibly more consequential finding — flag it back to the user rather than silently expanding this card's scope to "wire up the LLM port," which would need its own card.
