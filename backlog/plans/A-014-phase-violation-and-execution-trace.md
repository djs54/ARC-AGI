# A-014 - Phase Violations in Tool Calls and Empty Agent Execution Trace

## Card metadata

- Card: A014
- Priority: P1
- Depends on: A007

## Summary

Close two observability gaps: (1) the 8 phase-violation flags from `recall_lessons`/`upsert_lesson` called from disallowed phases, and (2) the empty `agent_execution_trace.json` plus missing Phoenix coverage during live runs. Either fix the call sites, or tighten the policy — but the `orchestration_report.violations` list must be empty after this card lands.

## Implementation approach

1. Identify each violating call site by grepping the solver/orchestrator code for `recall_lessons`, `recall_relevant_lessons`, and `upsert_lesson`. Cross-reference with `orchestration_report.tool_rules.<call>.allowed_phases`. For each call site:
   - decide whether the current phase is semantically correct and the rule should widen, OR the call should move to an allowed phase
   - annotate the decision in a short comment at the call site (only if the decision is non-obvious)
2. Update `orchestration_report.tool_rules` and/or the call sites accordingly.
3. Find the `agent_execution_trace.json` writer. It likely lives alongside `master_timeline.json` writer. Confirm the trace is opened, appended to per event, and closed at finalization. Fix any early-return that skips the flush.
4. Phoenix instrumentation:
   - locate the OTEL setup path (likely in `arc_runtime/llm.py` or a dedicated tracing module)
   - confirm exporter endpoint matches the SideQuests Phoenix instance the user runs (`http://127.0.0.1:6006` per the existing export)
   - confirm `arc.run` root span is emitted per puzzle
   - if Phoenix is optional, gate it on a clear env var and fail loud rather than silently dropping spans
5. Tests:
   - `tests/test_tool_phase_compliance.py` — simulates each phase and asserts tool calls do not violate `allowed_phases`
   - `tests/test_agent_execution_trace_writer.py` — runs a minimal puzzle and asserts the trace file is non-empty with the expected shape
   - `tests/test_phoenix_tracer_wiring.py` — asserts the OTEL setup initializes when the env var is set and emits a span; xfail or skip if Phoenix not running locally

## Concrete file additions/edits

- edit `agents/arc3/orchestrator.py`
- edit `agents/arc3/runner.py`
- edit wherever `agent_execution_trace.json` is written
- edit `arc_runtime/` OTEL wiring
- add `tests/test_tool_phase_compliance.py`
- add `tests/test_agent_execution_trace_writer.py`
- add `tests/test_phoenix_tracer_wiring.py`
- update `ARCHITECTURE.md` Runtime Notes with the instrumentation contract

## API/interface changes

- no MCP seam changes
- `orchestration_report.tool_rules` may grow allowed phases for some calls; document in ARCHITECTURE.md

## Tests to add or run

- `pytest -q tests/test_tool_phase_compliance.py`
- `pytest -q tests/test_agent_execution_trace_writer.py`
- `pytest -q tests/test_phoenix_tracer_wiring.py`
- one-puzzle smoke and confirm `violations == []` and `agent_execution_trace.json` is non-empty

## Validation commands

- `pytest -q -k "phase_compliance or execution_trace or phoenix_tracer"`
- `jq '.[0].orchestration_report.violations | length' submission_results_single.json` after a fresh smoke — expect `0`

## Assumptions/defaults

- Phoenix instance URL and project name are read from env (or a config file) and default to the values already used by SideQuests
- widening `allowed_phases` is acceptable when the call is semantically correct; tightening is preferred when it is not
- `agent_execution_trace.json` is an ARC-side artifact, not SideQuests-owned, and does not require MCP-seam changes
