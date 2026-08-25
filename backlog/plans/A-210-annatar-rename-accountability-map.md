# Plan: A210 — Rename Reasoner → Annatar, With a Recorded Accountability Map

## Card metadata

- ID: A210
- Priority: P1
- Layer: ARC runtime
- Dependencies: A200-A209

## Summary

Rename every live "Reasoner"/"reasoner" reference to "Annatar"/"annatar" across the codebase, leave historical backlog cards A200-A209 untouched (add one forward-pointer note instead of rewriting them), and produce `docs/annatar-accountability-map.md` recording, for every decision-point/phase/workflow in the system, whether it reports to Annatar today and why (or why not).

This plan is sized to run as **three sequential passes**, each independently haiku-executable, run one after another with a full test-suite check between passes (do not run them in parallel — later passes depend on earlier ones landing cleanly).

## Full reference inventory (confirmed via repo-wide search, 2026-08-25)

Approximately 500-600 occurrences across ~41 files. Full file-name renames required:

- `agents/arc4/investigation_reasoner.py` → `agents/arc4/annatar_state_machine.py`
- `agents/arc4/reasoner_signals.py` → `agents/arc4/annatar_signals.py`
- `tests/test_a200_investigation_reasoner_state_machine.py` → `tests/test_a200_annatar_state_machine.py`
- `tests/test_a202_reasoner_orchestrator_integration.py` → `tests/test_a202_annatar_orchestrator_integration.py`
- `tests/test_a205_reasoner_error_handling.py` → `tests/test_a205_annatar_error_handling.py`
- `tests/test_a209_budget_reasoner_routing.py` → `tests/test_a209_budget_annatar_routing.py`

Identifier renames (no file rename) in: `agents/arc4/workflow.py`, `agents/arc4/types.py`, `agents/arc4/ports.py`, `agents/arc4/plan_generator.py`, `agents/arc4/goal_resolver.py`, `arc_runtime/bundle.py`, `arc_runtime/dispatch.py`, `agents/arc4/telemetry.py`, `agents/arc4/graph_queries.py`, `agents/arc4/evaluator.py` (only if it references the string literal, confirm), `tests/test_a180_termination_classification.py`, `tests/test_a196_shift_a_c_trend_telemetry.py`, `tests/test_a203_anchor_biasing.py`, `tests/test_arc4_workflow.py` (only if it references anything renamed — confirm), `tests/test_a204_resume_crash_safety.py` (confirm), `tests/test_a208_entity_neighborhood_hard_exclusion.py` (confirm — likely not, but check).

Docs (living, rename in place): `ARCHITECTURE.md`, `docs/superpowers/specs/2026-08-23-trajectory-reasoner-design.md` (rename references in the body; the filename itself can stay dated as-is, since it's a historical spec filename convention — do not rename the file, just its prose content referring to "the Reasoner").

Docs (historical, do NOT rewrite): `backlog/A200.md` through `backlog/A209.md`, `backlog/plans/A-200-*.md` through `backlog/plans/A-209-*.md`. These stay exactly as they are — they're point-in-time records of what was built and reasoned about under the name that existed at the time.

## Technical approach

### Pass 1: Core runtime code (the actual rename)

1. Read every file in the identifier-rename list above, in full, before touching anything — confirm the exact current symbol names (line numbers will have shifted since this plan was written).
2. Rename, exactly:
   - `ReasonerOutcome` → `AnnatarOutcome`
   - `ReasonerPhase` → `AnnatarPhase`
   - `ReasonerDecision` → `AnnatarDecision`
   - `ReasonerLimits` → `AnnatarLimits`
   - `run_reasoner_cycle` → `run_annatar_cycle`
   - `compute_cycle_signals` — no rename needed (doesn't contain "reasoner"), but confirm during implementation this is really true
   - `WorkflowDependencies.reason` field → `WorkflowDependencies.annatar` (this is a public dataclass field threaded through `bundle.py`, every test's `WorkflowDependencies(...)` construction, and every `deps.reason = mock_reason`-style test assignment — this is the single highest-blast-radius rename in the whole card, budget extra care and re-grep after)
   - `WorkflowState.reasoner_anchor_hint` → `annatar_anchor_hint`
   - `WorkflowState.reasoner_degraded` → `annatar_degraded`
   - `WorkflowState.reasoner_unproductive_anchor_streak` → `annatar_unproductive_anchor_streak`
   - `WorkflowOrchestrator._route_budget_through_reasoner` → `_route_budget_through_annatar`
   - `WorkflowOrchestrator._route_second_veto_through_reasoner` → `_route_second_veto_through_annatar`
   - `PlanCandidate.metadata["reasoner_anchor_bias_applied"]` key → `"annatar_anchor_bias_applied"` (update the string literal in `plan_generator.py` and every test asserting on it)
   - `PlanCandidate.rationale` suffix text `"; reasoner requested retry"` → `"; annatar requested retry"` in `plan_generator.py`
   - The reason string `"reasoner_exhausted"` (set in `workflow.py` at both `_route_budget_through_annatar` and `_route_second_veto_through_annatar`, and in the normal terminate path) → `"annatar_exhausted"`. **Confirm this string is NOT a dict key anywhere it would silently stop matching** — re-run `.venv/bin/python -c "from agents.arc4.evaluator import classify_v2_termination; print(classify_v2_termination('terminated', 'annatar_exhausted'))"` after the rename and confirm it still prints `strategy_exhausted` (it will, since that classification falls through to the status-level `"terminated"` entry, not a reason-level key — but verify this directly rather than assuming).
3. Rename the two files (`git mv`), update every import site.
4. Do NOT touch `backlog/*.md` in this pass.
5. Run `make test-a && make test-all` — expect failures (tests haven't been updated yet), confirm the failures are all "renamed symbol not found"-shaped, not something unrelated.

### Pass 2: Tests

1. Update every test file that references any renamed symbol from Pass 1 — rename the four test files themselves (`git mv`), update every `import`, every mock/assertion referencing `deps.reason`, `state.reasoner_*`, `ReasonerOutcome(...)`, etc.
2. Pay specific attention to `tests/test_a180_termination_classification.py::test_reasoner_exhausted_reports_strategy_exhausted_not_crash` — rename the test itself (`test_annatar_exhausted_reports_strategy_exhausted_not_crash`) and its input string.
3. Run `make test-a && make test-all` — must be fully green after this pass. If anything still fails, it's either a missed rename or a real regression — diagnose which before proceeding, do not paper over a real failure by loosening an assertion.

### Pass 3: Docs and the accountability map

1. `ARCHITECTURE.md`: rename every "Reasoner"/"reasoner" occurrence to "Annatar"/"annatar" in the Graph-Engineering Principles section, the Implementation Track's A200-A209 entry, and anywhere else it appears. **While in that section, also fix the stale claim**: the current text lists `check_budget` alongside `termination_from_evaluation` as "deliberately-exempt environment-terminal... cases" — this predates A209's finding. Reword to something like: "`check_budget` reports to Annatar for visibility (so it can close out its own bookkeeping) but Annatar has no decision authority over the hard ceiling itself — see `backlog/A209.md`."
2. `docs/superpowers/specs/2026-08-23-trajectory-reasoner-design.md`: rename "Reasoner" references in the body prose (the filename itself stays as-is, it's a dated historical spec filename).
3. Add the forward-pointer note: a single short paragraph, either as a new top section in `backlog/A210.md` itself (recommended — keeps it in one place rather than editing ten historical files) stating plainly: "A200-A209 below refer to this component as 'the Reasoner.' It was renamed to 'Annatar' in A210. Those cards are left as their original historical record; read 'Reasoner' as 'Annatar' when cross-referencing them from anything written after 2026-08-25."
4. **Build `docs/annatar-accountability-map.md`.** One row (or subsection) per touchpoint below, each with: current status (reports to Annatar / structurally isolated / exempt by design), the mechanism or reason (with file:line), and a principle verdict using the `arc-graph-engineering-review` skill's checklist (invoke the skill for this step — don't freehand the principle analysis). Touchpoints to cover, from the A210 scoping investigation (2026-08-25):
   - The six core phases (perceive, resolve, plan, vet, execute, evaluate) — individually, not as one block; `vet` specifically needs its single-veto-vs-double-veto distinction called out, pointing at A212 for the open question.
   - Every `WorkflowOrchestrator.run()` control-flow exit: env-terminal, Annatar `terminate`, stall (no-Annatar legacy path), `CRASHED` exception handler (pointing at A211 for the fix), `_route_budget_through_annatar`'s three sub-cases, `_route_second_veto_through_annatar`'s three sub-cases.
   - Temporal workflows (`agents/arc4/temporal_workflows.py`) — exempt, deprecated/unused, note why.
   - World-model evaluation path (`benchmarks/arc3/world_model_eval.py`) — structurally isolated, but low-risk since it's read-only observational telemetry, not a decision-maker.
   - `agents/common/` — exempt, pure utilities, no decision logic.
   - `benchmarks/arc3/` broadly (trajectory_eval.py, outcome_judge.py, regression_monitor.py, ab_harness.py) — exempt by design, all offline/post-hoc analysis that structurally cannot report to Annatar (the episode is already over by the time they run).
   - The LLM escalation tiers inside `goal_resolver.py`/`plan_generator.py`'s `_query_llm` — reports to Annatar today, but only in fully-absorbed form via `state.active_goal`/`execution.candidate`, never with its own provenance preserved. Note this matches Shift B's "bounded sub-agent, raw results only" design intent — this is a confirmed-correct pattern, not a gap, and the map should say so explicitly rather than flagging it as suspicious just because the LLM's raw output doesn't separately surface.
5. Run `make test-a && make test-all` one final time to confirm the docs-only pass didn't somehow break anything (it shouldn't, but confirm rather than assume).
6. **Live-confirm**: run one live smoke test (`make smoke` or the direct `run_single_puzzle.py --live-smoke` invocation used throughout this session) after all three passes land, specifically watching for any `AttributeError`/`KeyError` that would indicate a renamed field's getter/setter pair was left inconsistent (e.g. something still reads `state.reasoner_anchor_hint` via a stale `getattr` default while the actual field is now `annatar_anchor_hint`, silently returning `None` forever instead of raising — this is the failure mode most likely to survive `make test-all` since a stale `getattr(..., "reasoner_anchor_hint", None)` doesn't error, it just silently stops working). Grep specifically for any remaining `getattr(state, "reasoner` or similar string-based attribute access pattern that wouldn't be caught by a straightforward identifier rename.

## Concrete file changes

See the full reference inventory section above — this plan's file list IS the concrete file-changes table, restated there to avoid duplication.

## Tests

No new tests for the rename itself — every existing test's continued passage (with renamed symbols) IS the test. Do add one new regression test in Pass 2: a test that directly imports `agents.arc4.annatar_state_machine` and `agents.arc4.annatar_signals` by their new module paths and confirms the import succeeds (catches a missed `git mv`/import-path update that a broader "does the suite pass" check might not isolate clearly).

## Validation commands

```bash
# After Pass 1 (expect failures, confirm they're all rename-shaped):
make test-a
make test-all

# After Pass 2 (must be fully green):
make test-a
make test-all
grep -rn "[Rr]easoner" agents/ arc_runtime/ tests/ | grep -v "^backlog/"

# After Pass 3 (docs + map):
make test-a
make test-all
grep -rn "[Rr]easoner" ARCHITECTURE.md docs/superpowers/specs/2026-08-23-trajectory-reasoner-design.md
ls docs/annatar-accountability-map.md

# Final live confirmation:
CAMPY_MCP_CMD="../hippocampy/.venv/bin/python -m campy.adapters.mcp_server" PYTHONPATH=. .venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 15
```

## Assumptions/defaults

- Persisted `WorkflowState` field names are renamed with no backward-compatibility shim for old serialized state (JSON artifacts, any in-flight Temporal workflow state). This is deliberate: these are per-run telemetry/resume fields, not a long-lived database schema, and nothing in this repo's current usage depends on reading old-named state after this rename lands. Document this choice explicitly in A210's Outcome section rather than letting it pass silently.
- If a symbol/reference is found during implementation that this plan's inventory missed, rename it too and note the addition in the Outcome section — the inventory above is a confirmed scoping pass, not guaranteed 100% exhaustive down to the last docstring.
