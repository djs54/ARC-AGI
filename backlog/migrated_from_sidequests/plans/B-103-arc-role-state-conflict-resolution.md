# B-103 — ARC Role-State Conflict Resolution in SolveEngine

## Metadata
- Card: B103
- Priority: P0
- Dependencies: B95, B97, B99, B101

## Summary

Fix persistent role-state management in `SolveEngine` so mutually exclusive ARC roles do not drift,
accumulate, or overwrite each other incorrectly across steps.

## Technical Approach

1. Replace or wrap the current confidence-only merge logic in `SolveEngine.solve()` with explicit
   role conflict handling.
2. Introduce role-state rules such as:
   - `player` and `goal` are mutually exclusive primary roles
   - a new conflicting role must clear or demote the old primary role before promotion
   - stale prior goals should be dropped or marked secondary when a stronger dominant goal appears
3. Preserve step-level role evidence, but maintain a coherent persistent summary:
   - one primary `player`
   - one primary `goal`
4. Surface role conflict resolution in solve/debug summaries when useful.
5. Add regression tests based on the observed live bug where a `player` later flipped to `goal`.

## Concrete File Changes

- `agents/arc3/solver.py`
  - replace naive confidence-only role merge with conflict-aware resolution
  - enforce single-primary role semantics
  - optionally expose conflict-resolution notes in strategy/debug output
- `tests/test_arc3_solver.py`
  - add focused unit/integration tests for role conflict resolution
- `agents/arc3/runner.py`
  - if needed, preserve conflict-resolution details in exported solve summaries
- `tests/test_arc3_durable_runner.py`
  - if needed, assert final summaries expose coherent single-primary roles

## API/Schema/Test Updates

- No schema changes expected.
- Export/debug shape may gain a small role-conflict-resolution note if needed.

## Acceptance Criteria

1. Persistent role merge is conflict-aware instead of confidence-only.
2. A player cannot silently become a goal unless conflict rules explicitly allow and explain it.
3. Final solve summaries contain at most one primary player and one primary goal.
4. Stale goals are demoted, removed, or clearly marked non-primary.
5. Focused tests cover at least 4 representative regression scenarios.

## Validation Commands

```bash
.venv/bin/pytest -q tests/test_arc3_solver.py tests/test_arc3_durable_runner.py tests/test_arc3_orchestrator.py tests/test_arc3_hypothesis.py
```

Then run one live puzzle-1 smoke test and inspect:
- `solve_phase_summary.object_roles`
- `solve_phase_summary.final_strategy_summary`
- step-level `solve_context.object_roles`

## Risks / Constraints

- This fix should not throw away useful step-level role evidence; it should only make the
  persistent solve summary coherent.
- Keep actions opaque. This is a role-state management fix, not a hard-coded semantic mapping pass.
