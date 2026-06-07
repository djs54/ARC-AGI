# Plan: A-121 — ARC v2 Plan Generator and Plan Vetter

## Card metadata

- **Card:** A121
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A118, A120
- **Intended executor:** GPT-5.4-mini subagent

## Summary

Implement the ARC v2 planning branch with a clear split: the generator proposes, the vetter gates. This card is where the parallel design starts paying off.

## Parallel contract

- Both modules consume A118 shared contracts and the A120 resolved-goal contract.
- Only the generator may use optional LLM help.
- The vetter must remain deterministic and side-effect free.

## Implementation approach

### Step 1: Implement `agents/arc4/plan_generator.py`

Responsibilities:

- gather action evidence from the injected query port
- rank candidate actions using resolved-goal context, untested-action priority, and bounded fallback probes
- include predicted effect and rationale in the returned `PlanCandidate`
- accept optional veto feedback from the previous vet pass and respect it during a single replan pass

### Step 2: Implement `agents/arc4/plan_vetter.py`

Responsibilities:

- evaluate the selected candidate against deterministic gate checks
- block repeated falsified actions when a viable untested alternative exists
- expose one structured alternative suggestion when vetoing
- surface warnings separately from hard vetoes when evidence is weak rather than clearly negative

### Step 3: Keep ownership boundaries sharp

The generator owns ranking and candidate proposal.

The vetter owns approval, veto reason, and alternative suggestion.

Neither module should update graph beliefs directly. That belongs to A122.

### Step 4: Add focused tests

Create `tests/test_arc4_planning.py` with cases for:

- untested actions ranking above stale repeated actions
- goal-conditioned ranking using the A120 goal contract
- veto on repeated falsification when a viable untested alternative exists
- allow path when no better alternative exists
- feedback loop from veto to single replan pass

## Concrete file edits

- `agents/arc4/plan_generator.py`
- `agents/arc4/plan_vetter.py`
- `tests/test_arc4_planning.py`

## Interface requirements

- consume only A118/A120 contracts
- generator returns A118 planning-result payload
- vetter returns A118 vet-decision payload

## Validation commands

```bash
pytest -q tests/test_arc4_planning.py
```

## Assumptions and defaults

- Query-port reads may return empty defaults in unit tests.
- LLM fallback, if implemented, must be off the hot path and covered by a trigger test rather than broad prompt testing.