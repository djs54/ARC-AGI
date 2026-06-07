# Plan: A-122 — ARC v2 Evaluator

## Card metadata

- **Card:** A122
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A118, A120
- **Intended executor:** GPT-5.4-mini subagent

## Summary

Implement the independent post-action evaluator for ARC v2. It judges outcomes, updates falsification state, and returns the next workflow decision.

## Parallel contract

- Consume A118 shared contracts and the A120 resolved-goal contract.
- Use only injected query-port methods for graph-side updates.
- Do not reuse planning code for evaluation logic.

## Implementation approach

### Step 1: Implement the evaluation pass

Responsibilities:

- compare predicted effect and actual effect
- decide whether progress was meaningful
- update workflow-state falsification counters for the current action family
- emit one of `continue`, `pivot`, or `terminate`

### Step 2: Define graph-side updates behind the query port

When evidence supports it, the evaluator should call query-port methods for:

- recording action effects
- confirming or contradicting hypotheses
- updating goal confidence
- recording reward-prediction error or equivalent negative feedback

All of these remain behind the injected port so the module is testable with fakes.

### Step 3: Keep state ownership explicit

Class-level falsification counts must update through `WorkflowState` or evaluator return data consumed by the workflow. Do not hide them in module-level globals.

### Step 4: Add focused tests

Create `tests/test_arc4_evaluator.py` for:

- prediction match path
- prediction falsification path
- pivot after repeated falsification without meaningful progress
- terminate on terminal game state
- reset or decay of falsification pressure after meaningful progress

## Concrete file edits

- `agents/arc4/evaluator.py`
- `tests/test_arc4_evaluator.py`

## Interface requirements

- consume only A118/A120 contracts
- return the A118 evaluation-result payload
- use only A118 query-port methods for external side effects

## Validation commands

```bash
pytest -q tests/test_arc4_evaluator.py
```

## Assumptions and defaults

- Any ambiguous-effect escalation should stay optional and small.
- A terminal-score helper may be extracted from existing ARC utilities if it can be reused without pulling in v1 orchestration behavior.