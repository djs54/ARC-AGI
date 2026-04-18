# A-015 - Add "Coverage-Saturated Abort" as a First-Class Failure Class

## Card metadata

- Card: A015
- Priority: P1
- Depends on: A010

## Summary

Introduce a new `FailureClass` entry distinguishing runs that ended because the action space was fully characterized but the directional plan did not reach the goal, from true `strategy_exhausted` thrashing. Route the saturation signal from the graduation gate (A010) into the run finalizer so this class is emitted deterministically.

## Implementation approach

1. Add `COVERAGE_SATURATED_ABORT = "coverage_saturated_abort"` to `agents/arc3/failure_taxonomy.py`.
2. In the run finalizer (search for where `strategy_exhausted` is currently assigned — likely `agents/arc3/runner.py` or the harness finalization path), emit the new class when:
   - the last `graduation_reason` string contains `coverage_saturated_high_confidence` (see A010), AND
   - the directional plan ran for at least `MIN_DIRECTIONAL_STEPS` (default 3) without producing a reward tick
3. Update `benchmarks/arc3/trajectory_eval.py` and `benchmarks/arc3/regression_monitor.py` to track the new class as a separate counter in `quality_dimensions.robustness`.
4. Update `outcome_judge.py` if/when the judge has a per-failure-class rubric adjustment.
5. Tests:
   - `tests/test_failure_class_coverage_saturated.py` — synthetic 1-action puzzle ending with the new class, not `strategy_exhausted`
   - regression tests in `tests/test_failure_taxonomy.py` to prevent mislabeling

## Concrete file additions/edits

- edit `agents/arc3/failure_taxonomy.py`
- edit `agents/arc3/runner.py` (or wherever finalization sets `failure_class`)
- edit `benchmarks/arc3/trajectory_eval.py`
- edit `benchmarks/arc3/regression_monitor.py`
- add `tests/test_failure_class_coverage_saturated.py`
- update `ARCHITECTURE.md` cognitive-model section to list the new class

## API/interface changes

- new enum value `COVERAGE_SATURATED_ABORT`
- new field `coverage_saturated_rate` in the robustness dict

## Tests to add or run

- `pytest -q tests/test_failure_class_coverage_saturated.py`
- `pytest -q tests/test_failure_taxonomy.py`

## Validation commands

- `pytest -q -k failure_class`
- confirm a re-run of the Apr 18 puzzle with A010 + A015 in place produces the new class, not `strategy_exhausted`

## Assumptions/defaults

- `MIN_DIRECTIONAL_STEPS = 3` is a reasonable floor before declaring the directional plan "tried and failed"
- existing `strategy_exhausted` semantics remain for runs that *did* thrash through multiple strategies without reaching saturation
- the `regression_monitor` will alert on either rate crossing its threshold independently
