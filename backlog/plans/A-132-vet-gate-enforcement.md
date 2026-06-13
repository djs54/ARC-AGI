# Plan: A-132 — Vet Gate Enforcement

## Context

The vet phase identifies weak evidence but approves anyway. It's a diagnostic message without teeth.

## Approach

### 1. Add attempt-count-aware veto logic

The vet has access to `state.action_attempt_counts`. Add a rule:

```python
if attempt_count >= 3 and evidence_strength == "weak":
    approved = False
    reason = f"vetoed: {action_id} tried {attempt_count} times with weak evidence"
```

### 2. Wire graph evidence queries

Call `arc_check_action_gate` to get a quantitative evidence score. Use this instead of (or alongside) the LLM's qualitative "weak evidence" label.

### 3. Ensure replan produces different action

When the vet vetoes, the replan path in temporal_workflows.py re-runs resolve → plan → vet. The replanned action must differ from the vetoed one. Add the vetoed action to a `recently_vetoed` set in state so the planner can exclude it.

### 4. Test the replan path

Add a unit test that mocks the vet to veto on the first pass and verifies the replan path fires and produces a different action.

## Files to modify

- `agents/arc4/vet.py` — threshold logic, evidence query
- `agents/arc4/types.py` — add `recently_vetoed_actions` to WorkflowState if needed
- `agents/arc4/planner.py` — respect vetoed action exclusions
- `tests/` — replan path test

## Risks

- Too-aggressive veto could cause premature skips (double-veto → skip). Need the replan to actually diversify.
