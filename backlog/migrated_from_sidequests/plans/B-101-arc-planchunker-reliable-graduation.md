# B-101 — ARC PlanChunker Reliable Graduation

## Metadata
- Card: B101
- Priority: P1
- Dependencies: B95, B97, B99, B100

## Summary

Make `PlanChunker` graduation from `explore` to `directional` reliable across puzzle variants by
replacing fragile gating with an explicit graduation equation and visible debug reasons.

## Technical Approach

1. Define a graduation equation in `PlanChunker` that combines:
   - player confidence
   - goal confidence
   - exploration coverage/completeness
   - action/path evidence quality
   - contradiction penalties
2. Add a structured `graduation_reason` payload to chunk/debug context so exports show why the
   chunk stayed `explore` or became `directional`.
3. Prefer `directional` when:
   - player and goal are both present above threshold
   - exploration is substantially complete or the remaining exploration has low expected value
   - no strong contradictory evidence exists
4. Keep `explore` when:
   - role evidence is weak
   - goal candidate is unstable
   - contradictory action/path evidence is high
5. Add regression tests from live patterns where graduation previously failed despite strong
   player+goal evidence.

## Concrete File Changes

- `agents/arc3/solver.py`
  - make chunk graduation score-driven
  - attach structured graduation reasons to chunk/solve context
- `tests/test_arc3_solver.py`
  - add focused tests for graduation success/failure cases
- `agents/arc3/runner.py`
  - if needed, preserve the new graduation reason in exported solve summaries/debug output
- `tests/test_arc3_durable_runner.py`
  - if needed, assert the new graduation reason is visible in exports

## API/Schema/Test Updates

- No schema changes expected.
- Export/debug shape may gain one structured `graduation_reason` object if needed.

## Acceptance Criteria

1. `PlanChunker` uses a documented graduation score or thresholded decision.
2. Strong player+goal evidence with mostly-complete exploration reliably yields `directional`.
3. Weak or contradictory evidence reliably yields `explore`.
4. Debug/export output records why the decision was made.
5. Focused tests cover at least 4 representative scenarios.

## Validation Commands

```bash
.venv/bin/pytest -q tests/test_arc3_solver.py tests/test_arc3_durable_runner.py tests/test_arc3_orchestrator.py tests/test_arc3_hypothesis.py
```

Then run one live puzzle-1 smoke test and inspect:
- `solve_phase_summary.final_strategy_summary`
- solve-context chunk mode per step
- any structured graduation reason added to exports

## Risks / Constraints

- The chunker still must not hard-code action semantics.
- Graduation should not eliminate exploration entirely; it should just stop unnecessary persistence
  in explore mode once solve evidence is strong enough.
