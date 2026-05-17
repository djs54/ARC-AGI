# Plan: A-072 — harden runner solve-context access for dict and object shapes

## Card metadata

- **Card:** A072
- **Priority:** P1
- **Layer:** ARC runtime
- **Depends on:** A038, A065

## Summary

Eliminate dict/object solve-context crashes by centralizing access and replacing direct attribute reads in runner packaging paths.

## Implementation approach

1. Audit solve-context reads in `agents/arc3/runner.py`.
2. Use or extend an existing helper such as `_solve_context_get(sc, field, default)`.
3. Replace direct attribute reads in:
   - final result packaging
   - failure taxonomy inputs
   - live/progress snapshots
   - eval layer construction
4. Preserve object behavior for dataclass-style solve contexts.
5. Add regression tests for dict-shaped and object-shaped contexts.

## Concrete file additions/edits

- `agents/arc3/runner.py`
  - Centralize and apply safe solve-context accessor.
- `agents/arc3/orchestrator.py`
  - Optional: normalize solve-context export shape if needed.
- `tests/test_arc3_durable_runner.py`
  - Add dict-shaped solve-context packaging regression.
- `tests/test_b185_failure_taxonomy.py`
  - Ensure classification receives safe defaults.

## API/interface changes

No external API changes. Internal helper behavior should support:

- dict
- dataclass/object
- `None`

## Tests to add or run

Validation commands:

```bash
pytest -q tests/test_arc3_durable_runner.py tests/test_b185_failure_taxonomy.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Missing booleans default to `False`.
- Missing numeric scores default to `0.0`.
- Missing nested workspace data defaults to absent/empty, not an error.
