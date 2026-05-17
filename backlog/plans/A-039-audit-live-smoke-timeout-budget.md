# Plan: A-039 — audit live-smoke timeout budget

## Card metadata

- **Card:** A039
- **Priority:** P1
- **Layer:** evaluation/harness
- **Depends on:** A007, A038

## Summary

Determine whether live smokes are failing due to per-call LLM timeout or overall wall-clock budget, then tune defaults and logging so timeout failures are interpretable and actionable.

## Implementation approach

1. Instrument timeout decision points with explicit reason tags.
2. Reproduce a long smoke and capture which guard triggers first.
3. Adjust default budget values and/or guard ordering for one-puzzle smoke profile.
4. Preserve A007 failure taxonomy boundaries (`llm_timeout` vs `tool_timeout`).
5. Add tests for timeout classification and budget-trigger messaging.

## Concrete file edits

- `agents/arc3/runner.py`
  - Emit explicit timeout-trigger reason in result payload/log.
- `run_single_puzzle.py`
  - Ensure smoke defaults are coherent with expected local throughput.
- `arc_runtime/llm.py` (if per-call timeout default needs adjustment)
- `tests/test_run_single_puzzle_cli.py`
- `tests/test_b185_failure_taxonomy.py`

## API / interface changes

- Optional: add a descriptive timeout reason field to exported result metadata.
- CLI flags remain backward-compatible.

## Tests to run

- `pytest -q tests/test_run_single_puzzle_cli.py tests/test_b185_failure_taxonomy.py`
- `python run_single_puzzle.py --num-puzzles 1 --max-steps 50 --card-id timeout_budget_verify`

## Validation commands

- `rg -n "llm_timeout|tool_timeout|wall-clock|budget" agent_execution_trace.json submission_results_single.live.jsonl`

## Assumptions / defaults

- Local smoke baseline uses Ollama and may have variable per-step latency.
- Target is diagnosis clarity plus sane defaults, not hiding genuine model latency failures.
