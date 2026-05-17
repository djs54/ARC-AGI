# Plan: A-060 — live-smoke telemetry regressions for overrides and violations

## Card metadata

- **Card:** A060
- **Priority:** P2
- **Layer:** evaluation/harness
- **Depends on:** A054, A057, A058

## Summary

Patch the remaining live-smoke telemetry regressions: null structured override reasons despite policy rewrites, and false/noisy `orchestration_status=violation` caused by unknown-phase memory calls.

Graph-solution classification: this is testing/provenance work. The graph-memory system should be able to explain decisions with compact evidence paths, and telemetry should distinguish fresh graph evidence, cached graph evidence, broad text recall, and no memory evidence.

## Implementation approach

1. Build a regression fixture from the 2026-04-24 smoke shape:
   - `policy_override` progress entry where rationale says ACTION7 is blocked but `override_reason` is null
   - orchestration report with `recall_lessons` in phase `unknown`
2. Audit all action rewrite paths after A058:
   - policy override
   - autopilot
   - guard override
   - replan-forced probe
3. Normalize rewrite metadata into a single structured field set before progress logging:
   - `candidate_action`
   - `selected_action`
   - `executed_action`
   - `decision_source`
   - `override_reason`
   - optional `override_policy_id`
   - optional `override_trigger`
4. Patch orchestration scoring so bootstrap/startup calls are phase-attributed or suppressed only with explicit reason. Keep true runtime unknown-phase calls as violations.
5. Update final report consistency so `status` reflects post-suppression violation state.
6. Add memory provenance fields to override/report fixtures:
   - `memory_prior_source`
   - `memory_prior_path_summary`
   - `memory_prior_confidence`
   - `memory_prior_graph_key`
   - `memory_prior_hop_bound`
7. Ensure graph-memory evidence is treated as a provenance path, not free-form rationale text.

## Concrete file additions/edits

- `agents/arc3/orchestrator.py`
  - Normalize rewrite metadata at action selection boundaries.
- `agents/arc3/runner.py`
  - Preserve normalized rewrite metadata and memory provenance in `progress_log` and timeline payloads.
- `benchmarks/arc3/trajectory_eval.py`
  - Fix unknown-phase handling and status calculation.
- `benchmarks/arc3/adapter.py`
  - Preserve phase attribution in sidequests ledger / orchestration report inputs.
- `tests/test_a060_live_smoke_telemetry.py`
  - New fixture-driven tests for null override reasons and unknown-phase false violations.
- Existing tests:
  - `tests/test_arc3_orchestrator.py`
  - `tests/test_a042_reproduce_false_positive.py`

## API/interface changes

- No external API changes.
- Internal progress-log/report fields should remain backward compatible; add optional structured fields rather than removing existing ones.
- Optional provenance fields:
  - `memory_prior_source: graph|text|cache|none`
  - `memory_prior_path_summary`
  - `memory_prior_graph_key`
  - `memory_prior_hop_bound`
  - `memory_prior_confidence`

## Tests to add or run

Add tests for:

- policy override with free-form rationale but missing structured reason fails pre-fix and passes post-fix
- all rewrite decision sources require non-null structured reasons
- bootstrap `recall_lessons` in an initially unknown phase is either attributed or suppressed with reason
- true disallowed runtime phase/tool call remains a violation
- final `orchestration_report.status` is `ok` when all violations are suppressed and `violation` when any true violation remains
- memory-influenced overrides have structured provenance; free-form rationale alone fails the fixture
- graph-memory provenance includes bounded path metadata and a selective graph key

Validation commands:

```bash
pytest -q tests/test_a060_live_smoke_telemetry.py tests/test_arc3_orchestrator.py tests/test_a042_reproduce_false_positive.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- A058 may move or rename action arbitration helpers; implement A060 after A058 to avoid duplicate metadata patches.
- Bootstrap/startup attribution should be narrow: do not whitelist arbitrary `unknown` phase runtime calls.
- This card should not change solver behavior except for preserving structured telemetry fields.
- If SideQuest cannot yet return graph path summaries, ARC should emit `memory_prior_source=text|none` and the implementation should file the sibling memory capability follow-up rather than faking graph provenance.
