# A-010 - Graduation Score Cannot Escape Low-Evidence Floor in Single-Action Games

## Card metadata

- Card: A010
- Priority: P0
- Depends on: A007, A008

## Summary

Rework the graduation gate so the solver can exit `Explore` and commit to a directional plan when structural confidence is high and the action space is fully characterized, rather than being permanently capped by the evidence floor and progress decay.

## Implementation approach

1. In `PlanChunker._graduation_assessment` (`agents/arc3/solver.py:1307-1490`) compute a new boolean `coverage_saturated = (action_coverage.get("initial_exploration_complete") or coverage_ratio >= 1.0) and untested_count == 0`.
2. Make the B142 evidence-floor cap (`solver.py:1405-1411`) skip when `coverage_saturated` is true — once all actions are sampled, absence of evidence is not a reason to keep exploring.
3. Make the B142 progress-decay penalty (`solver.py:1414-1419`) use `min(0.25, 0.05 * consecutive_zero_reward_steps)` — still punishes but does not swamp the structural terms.
4. Extend the emergency "stuck + high geometry" branch (`solver.py:1394-1396`) so when `coverage_saturated and geometry_high_conf`, graduation `ready=True` regardless of `score >= GRADUATION_THRESHOLD`, and `graduation_reason` records `"coverage_saturated_high_confidence"`.
5. Add tests in `tests/test_solver_graduation.py` (new file) covering:
   - single-action-available with high confidence → graduates within 3 steps
   - multi-action, coverage incomplete → still stays in explore
   - multi-action, coverage complete but low confidence → still stays in explore
   - regression: existing graduation cases in the test suite still pass

## Concrete file additions/edits

- edit `agents/arc3/solver.py`
  - add `coverage_saturated` computation and pass into floor/decay gates
  - extend graduation ready branch with the saturation bypass
  - expand graduation trace to include `coverage_saturated` and `pre_cap_score`
- add `tests/test_solver_graduation.py`
- update `ARCHITECTURE.md` cognitive-model section only if user-visible behavior changes

## API/interface changes

- no public interface change
- internal `graduation_assessment` return dict gains `coverage_saturated: bool`

## Tests to add or run

- `pytest -q tests/test_solver_graduation.py`
- `pytest -q tests/test_orchestrator_replan_loop.py` (regression)
- re-run the one-puzzle smoke and confirm `failure_class != strategy_exhausted` when structural confidence is high

## Validation commands

- `pytest -q tests/test_solver_graduation.py`
- `pytest -q -k graduation`

## Assumptions/defaults

- `action_coverage` is already produced upstream and includes `initial_exploration_complete`, `tested_count`, `untested_count`, `top_two_low_value`
- graduation threshold `0.72` remains the multi-action target; the new branch bypasses the threshold only under the saturation + geometry precondition
- we do not change how `evidence_score` itself is computed — only how it gates the final `ready` decision
