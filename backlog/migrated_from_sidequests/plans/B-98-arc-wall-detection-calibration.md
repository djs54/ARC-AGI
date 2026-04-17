# B-98 — ARC Wall Detection Calibration

## Metadata
- Card: B98
- Priority: P1
- Dependencies: B95, B97

## Summary

Reduce false-positive `wall` labels in the ARC solve phase by requiring stronger multi-signal
evidence before promoting a color to obstacle status.

## Technical Approach

1. Split stable-color reasoning into:
   - `wall`
   - `structure`
   - `unknown`
2. Score wall evidence from:
   - static-row or HUD coverage
   - repeated immobility across frames
   - lack of changed-region participation
   - low count-delta / low operator interaction
3. Only promote to `wall` when at least two strong signals agree.
4. Keep stable-but-ambiguous colors as `structure` or `unknown`.

## Files to Modify

- `agents/arc3/solver.py`
- `tests/test_arc3_solver.py`

## Validation

```bash
.venv/bin/pytest -q tests/test_arc3_solver.py tests/test_arc3_orchestrator.py tests/test_arc3_hypothesis.py
```

Then run one live puzzle-1 smoke test and inspect `solve_phase_summary.object_roles`.
