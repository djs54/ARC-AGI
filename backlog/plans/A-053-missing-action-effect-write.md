# Plan: A-053 — missing action-effect write

## Card metadata

- **Card:** A053
- **Priority:** P1
- **Layer:** ARC runtime
- **Depends on:** A052

## Summary

Guarantee action-effect telemetry emission for every executed action, including autopilot steps.

## Implementation approach

1. Reproduce missing-event step from trace.
2. Inspect branching paths (autopilot vs sandbox vs override) for skipped writer calls.
3. Ensure writer call happens exactly once per executed step.
4. Add explicit error marker when write attempt fails.

## Concrete file edits

- `agents/arc3/runner.py`
- `agents/arc3/orchestrator.py`
- `sidequest_mcp_client/observability.py`
- `tests/test_arc3_durable_runner.py`

## Tests to run

- `pytest -q tests/test_arc3_durable_runner.py`
