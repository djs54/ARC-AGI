# Plan: A-046 — relabel timeout failure class

## Card metadata

- **Card:** A046
- **Priority:** P1
- **Layer:** evaluation/harness
- **Depends on:** A039, A044

## Summary

Differentiate total wall-clock budget exhaustion from true LLM timeout in failure classification and reporting.

## Implementation approach

1. Enumerate timeout exit paths in runner/harness.
2. Assign distinct taxonomy class for wall-clock budget exhaustion.
3. Keep LLM call timeout mapped to `llm_timeout`.
4. Update exported summary fields and downstream report mapping.
5. Add regression tests for both paths.

## Concrete file edits

- `agents/arc3/failure_taxonomy.py`
- `agents/arc3/runner.py`
- `run_single_puzzle.py`
- `tests/test_b185_failure_taxonomy.py`
- `tests/test_run_single_puzzle_cli.py`

## API / interface changes

- New/renamed failure class string in outputs: `wall_clock_budget_exhausted`.

## Tests to run

- `pytest -q tests/test_b185_failure_taxonomy.py tests/test_run_single_puzzle_cli.py`

## Validation commands

- `rg -n "llm_timeout|wall_clock_budget_exhausted|timeout" submission_results_single.live.jsonl agent_execution_trace.json`

## Assumptions / defaults

- Existing consumers tolerate a new failure-class value when documented.
