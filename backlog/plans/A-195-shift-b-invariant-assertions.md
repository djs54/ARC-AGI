# Plan: A195 — Assert the Shift-B Invariant on Real Run Data

## Card metadata

- ID: A195
- Priority: P2
- Layer: ARC runtime
- Dependencies: A191, A184, A188

## Summary

Add a pure, deterministic invariant check confirming no executed candidate was `repeated_falsified` — the guarantee A191 is supposed to make true by construction — and surface any violation through telemetry plus a standalone post-run gate script, so a regression in the falsification-guard family (A182/A184/A185/A187/A188/A189/A191) is caught automatically on the next run instead of requiring a human to notice it in a live trace.

## Technical approach

### 1. `agents/arc4/compliance_checks.py` (new)

```python
"""Deterministic post-execution checks for the graph-control-plane design
principles (Shift A/B/C, graph-engineering review, 2026-08-22). Pure
functions only -- no LLM, no graph call, inspect data already in hand."""

from __future__ import annotations

from typing import Mapping

from .types import ExecutionResult


def check_shift_b_invariants(execution: ExecutionResult) -> list[str]:
    """Shift B: no module (least of all a one-shot LLM patch) should be able
    to smuggle a graph-falsified candidate into execution. Once A191 excludes
    repeated_falsified candidates from ever being built, this should always
    return []; a non-empty result means that guarantee broke somewhere
    upstream (A184's patch guard, A188's vetter veto, or A191's pre-filter)."""
    violations: list[str] = []
    candidate = execution.candidate
    if candidate is not None:
        metadata = candidate.metadata if isinstance(candidate.metadata, Mapping) else {}
        if bool(metadata.get("repeated_falsified")):
            violations.append(
                f"executed candidate action_id={candidate.action_id!r} book_id={getattr(candidate, 'book_id', candidate.action_id)!r} "
                "was repeated_falsified -- A191's pre-filter (or an earlier guard) should have excluded it before execution"
            )
    return violations
```

Read `agents/arc4/types.py`'s `ExecutionResult` and `PlanCandidate` definitions in full before implementing to confirm exact field names and whether `book_id` is available directly on `candidate` (per A190, if that card has landed first) or must be read from `metadata.get("book_id")` (if A190 hasn't landed yet) — write the check to work correctly either way (prefer `getattr(candidate, "book_id", None) or metadata.get("book_id") or candidate.action_id` if A190's status is uncertain at implementation time).

### 2. Wire into `evaluator.py::evaluate()`

Read `evaluator.py::evaluate()` in full before editing to confirm the current line numbers and exactly where `EvaluationResult` is constructed and returned. Add, near the end of `evaluate()` before constructing the final `EvaluationResult`:

```python
from .compliance_checks import check_shift_b_invariants
...
compliance_violations = check_shift_b_invariants(execution)
```

Thread `compliance_violations` into the `EvaluationResult`'s `metadata` dict at construction (e.g. `metadata={**existing_metadata, "compliance_violations": compliance_violations}` — match whatever pattern `evaluate()` already uses to build its `EvaluationResult.metadata`, don't invent a new one). This must not change `decision`, `reason`, `falsification_delta`, or any other existing field — purely additive.

### 3. `telemetry.py` — surface it per step

In `_step_snapshot` (`telemetry.py:133-199`), add:

```python
"compliance_violation_count": len(evaluation.metadata.get("compliance_violations", [])) if evaluation is not None and isinstance(evaluation.metadata, Mapping) else 0,
```

Placed alongside the existing `evaluation is not None` block (~line 189-196) or as a top-level snapshot field defaulting to `0` — follow whichever placement matches the existing style of similar evaluation-derived fields (e.g. `falsification_delta`, `failure_reason`) most closely.

### 4. `scripts/check_compliance_violations.py` (new)

A small standalone script, no new dependencies:

```python
#!/usr/bin/env python
"""Exit non-zero if any step in an ARC v2 trace recorded a Shift-B compliance
violation. Usage: python scripts/check_compliance_violations.py [trace_path]
Defaults to the most recently modified agent_execution_trace.json under the
smoke output directory if no path is given."""
import json
import sys
from pathlib import Path


def main() -> int:
    trace_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _find_latest_trace()
    if trace_path is None or not trace_path.exists():
        print(f"No trace file found at {trace_path}", file=sys.stderr)
        return 2
    violations_found = []
    with trace_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            count = row.get("compliance_violation_count", 0)
            if count:
                violations_found.append((row.get("step"), count))
    if violations_found:
        print(f"COMPLIANCE VIOLATIONS in {trace_path}:", file=sys.stderr)
        for step, count in violations_found:
            print(f"  step {step}: {count} violation(s)", file=sys.stderr)
        return 1
    print(f"No compliance violations in {trace_path}")
    return 0


def _find_latest_trace() -> Path | None:
    ...  # confirm actual smoke output directory/filename convention by reading
    # run_single_puzzle.py / telemetry.py's append_snapshot wiring before
    # implementing -- do not guess a path


if __name__ == "__main__":
    raise SystemExit(main())
```

Before implementing `_find_latest_trace`, read `run_single_puzzle.py` and `telemetry.py`'s `append_snapshot` callback wiring to confirm the actual trace file format (one JSON object per line? a single JSON array?) and default output path/directory — the sketch above assumes JSON-lines based on `docs/trace_recipes.md`'s existing jq recipes operating on `agent_execution_trace.json`, but confirm before writing, don't assume.

### 5. `Makefile` — additive only

```makefile
check-compliance: ## exit non-zero if the most recent smoke trace recorded a Shift-B violation
	$(PYTHON) scripts/check_compliance_violations.py
```

Do not modify the existing `smoke`, `test-a`, or `test-all` targets.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/compliance_checks.py` (new) | `check_shift_b_invariants(execution) -> list[str]` |
| `agents/arc4/evaluator.py` | `evaluate()` calls the check, attaches result to `EvaluationResult.metadata["compliance_violations"]` |
| `agents/arc4/telemetry.py` | `_step_snapshot` adds `compliance_violation_count` |
| `scripts/check_compliance_violations.py` (new) | Standalone trace-file gate script |
| `Makefile` | New `check-compliance` target (additive only) |
| `tests/test_a195_shift_b_invariant_assertions.py` (new) | Coverage (see Tests) |

## Tests

New `tests/test_a195_shift_b_invariant_assertions.py`:

1. `check_shift_b_invariants` returns `[]` for an `ExecutionResult` whose candidate has `metadata={"repeated_falsified": False}` (or the key absent entirely).
2. `check_shift_b_invariants` returns exactly one violation string for a candidate with `metadata={"repeated_falsified": True}`, and the string contains the candidate's `action_id`.
3. `check_shift_b_invariants` returns `[]` for an `ExecutionResult` with `candidate=None` (defensive — should not crash).
4. `evaluator.py::evaluate()` end-to-end: an execution whose candidate is `repeated_falsified` produces an `EvaluationResult` with a non-empty `metadata["compliance_violations"]`, while `decision`/`reason`/`falsification_delta` are unaffected (reuse an existing evaluator test's setup and only add the new assertion, to confirm no regression).
5. `telemetry.py::_step_snapshot` reflects `compliance_violation_count` correctly for both a clean and a violating evaluation.
6. `scripts/check_compliance_violations.py`, invoked as a subprocess or imported and called directly, exits `0` against a fixture trace file with no violations and non-zero against one with a violation on some step (construct both fixture files in the test, don't depend on a real `make smoke` run).

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a195_shift_b_invariant_assertions.py -v
.venv/bin/python -m pytest tests/test_observability.py tests/test_trace_durability.py -v
make test-a
make test-all
make smoke && make check-compliance
```

## Assumptions/defaults

- Violations are recorded, never raised — a live episode must not crash because this check fired. The enforcement point is the post-run gate script (`check-compliance`), suitable for CI or a manual post-smoke step, not an in-episode hard stop.
- If A190 (first-class `book_id` field) hasn't landed by the time this card is implemented, `check_shift_b_invariants` must still work correctly reading `book_id` from `candidate.metadata` — write it defensively (see step 1) rather than assuming A190's field exists.
- The trace file format/path assumptions in `scripts/check_compliance_violations.py` must be confirmed against `run_single_puzzle.py`/`telemetry.py`'s actual wiring during implementation, not assumed from this plan's sketch.
