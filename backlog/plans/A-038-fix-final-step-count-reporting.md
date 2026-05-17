# Plan: A-038 — fix final step count reporting

## Card metadata

- **Card:** A038
- **Priority:** P0
- **Layer:** evaluation/harness
- **Depends on:** A022, A033

## Summary

Fix the mismatch where runtime executes actions but final packaged results report `steps: 0`, which currently suppresses trajectory scoring and distorts run summaries.

## Implementation approach

1. Trace the source of truth for executed step count in `runner` result payloads.
2. Trace the packaging path in `run_single_puzzle.py` and confirm where `steps` is derived.
3. Replace stale/default step sourcing with runtime-derived count.
4. Add a defensive fallback in trajectory evaluator only if needed (prefer fixing producer over consumer hacks).
5. Add regression coverage using a small deterministic run fixture.

## Concrete file edits

- `agents/arc3/runner.py`
  - Ensure final per-task result includes authoritative executed-step count.
- `run_single_puzzle.py`
  - Use authoritative step field when writing summaries/export artifacts.
- `benchmarks/arc3/trajectory_eval.py` (optional)
  - Accept corrected field and avoid skipping when valid non-zero actions exist.
- `tests/test_run_single_puzzle_cli.py` or `tests/test_arc3_durable_runner.py`
  - Add regression asserting summary steps equals executed actions.

## API / interface changes

- No public CLI/API changes.
- Internal result payload contract: `steps` must equal executed action count.

## Tests to run

- `pytest -q tests/test_run_single_puzzle_cli.py`
- `pytest -q tests/test_arc3_durable_runner.py`
- `python run_single_puzzle.py --num-puzzles 1 --max-steps 20 --card-id step_count_verify` (local smoke)

## Validation commands

- `rg -n "\"steps\": 0|trajectory score not computed" submission_results_single.live.jsonl agent_execution_trace.json`

## Assumptions / defaults

- Runtime already tracks executed actions accurately; bug is packaging/reporting mismatch.
- Keep changes minimal and scoped to result accounting paths.
