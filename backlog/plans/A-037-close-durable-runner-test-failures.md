# Plan: A-037 — close durable runner test failures

## Goal

Fix two test-only failures in `tests/test_arc3_durable_runner.py` to align test expectations with production behavior and resolve path resolution issues.

## Precondition

Both test failures are due to:
1. Test assertion expecting old behavior (only successful results returned)
2. Path assumption that doesn't hold when sidequests package is pip-installed

## Steps

1. **Fix test_continues_after_task_failure (line 145)**
   - Root cause: Production now returns all results (failed + successful), but test expected only successful
   - Change assertion from `assert len(results) == 1` to `assert len(results) == 2`
   - Add shape validation for both results: task_id, correct flag, and failure_class for failed result
   - Keep existing checkpoint assertions (lines 147-152) intact

2. **Fix test_upsert_lesson_round_trip (line 833-835)**
   - Root cause: Path computed relative to this repo doesn't exist; file is in sibling sidequests-brain repo
   - Add `import sidequests` at top of test function
   - Change SEED_PATH from `Path(__file__).resolve().parents[1] / "sidequests/data/..."` to `Path(sidequests.__file__).resolve().parent / "data" / "GistSeedExamples.md"`
   - This resolves the path through the installed package, which is the canonical location

3. **Verify target tests pass**
   - Run both fixed tests individually
   - Confirm they pass

4. **Verify full test file (23 tests)**
   - Run entire durable_runner test file
   - All tests must pass (no regressions)

5. **Verify A-baseline (18/18)**
   - Run `make test-a`
   - Confirm 18/18 still pass

## Why These Changes

### test_continues_after_task_failure

The production runner's behavior changed at some point to return all task results, including failures. This makes sense because:
- Callers need visibility into which tasks failed and why
- The checkpoint already tracks success/failure status
- Returning all results gives a complete picture of the batch

The test name "continues_after_task_failure" documents that the runner continues past failures. The correct assertion is `len(results) == 2` — both tasks are included in the output.

### test_upsert_lesson_round_trip

The path resolution was hardcoded relative to the ARC_AGI repo, but the GistSeedExamples.md file is actually part of the sidequests-brain package. When the package is installed via pip (as documented in README.md and CLAUDE.md), the canonical path is through the package's `__file__` location, not a relative sibling repo path.

Using `Path(sidequests.__file__).resolve().parent` is:
- More portable (works regardless of sibling repo state)
- Consistent with Python packaging conventions
- Maintainable (if the package structure changes, imports adapt automatically)

## Expected Outcome

- test_continues_after_task_failure: PASSED (updated assertion + shape checks)
- test_upsert_lesson_round_trip: PASSED (correct path resolution)
- All 23 tests in durable_runner file: PASSED (no regressions)
- A-baseline (make test-a): 18/18 PASSED (unchanged)
