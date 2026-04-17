# B-99 — ARC Goal Detection from Target Evidence

## Metadata
- Card: B99
- Priority: P1
- Dependencies: B95, B97, B98

## Summary

Strengthen goal inference so the solve phase can identify or rank target objects from board
evidence instead of staying generic.

## Technical Approach

1. Add goal-candidate scoring to `ObjectRoleMapper` or a dedicated goal-inference helper.
2. Use evidence such as:
   - small stationary object score
   - changed-region convergence near candidate
   - path-hypothesis convergence
   - exclusion from walls / background / HUD
3. Expose ranked goal candidates to the solve context even when confidence is not yet high enough
   for a final `GOAL` label.
4. Let `VictoryHypothesizer` consume ranked goal evidence instead of only final hard labels.

## Files to Modify

- `agents/arc3/solver.py`
- `tests/test_arc3_solver.py`

## Validation

```bash
.venv/bin/pytest -q tests/test_arc3_solver.py tests/test_arc3_orchestrator.py tests/test_arc3_hypothesis.py
```

Then run one live puzzle-1 smoke test and inspect the exported solve summary for a concrete goal
candidate or ranked target evidence.
