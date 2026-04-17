# B-100 — ARC Directed Chunking Graduation

## Metadata
- Card: B100
- Priority: P1
- Dependencies: B95, B97, B99

## Summary

Teach `PlanChunker` and `SolveEngine` to switch from exploration to directed movement once the
solve context becomes strong enough.

## Technical Approach

1. Define explicit chunk modes:
   - `explore`
   - `directed`
2. Add a graduation rule:
   - if `player` exists and a concrete goal candidate exists, prefer directed chunks
   - if path or action trends indicate a region to approach, use that as a short-horizon target
3. Keep explore mode only when evidence is insufficient.
4. Export chunk mode clearly in `solve_phase_summary` and prompt solve context.

## Files to Modify

- `agents/arc3/solver.py`
- `tests/test_arc3_solver.py`
- `agents/arc3/orchestrator.py`

## Validation

```bash
.venv/bin/pytest -q tests/test_arc3_solver.py tests/test_arc3_orchestrator.py tests/test_arc3_hypothesis.py
```

Then run one live puzzle-1 smoke test and verify the solve chunk becomes directed once player+goal
evidence exists.
