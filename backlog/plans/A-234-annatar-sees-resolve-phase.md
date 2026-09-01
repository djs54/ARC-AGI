# A234 — Annatar Sees the Resolve Phase: Plan

## Card metadata

- Card: `backlog/A234.md`
- Depends on: A230 (the exact routing pattern this card reuses)

## Design (settled here, mirroring A230's own precedent)

Confirmed by direct read before writing this plan: the normal per-cycle Annatar call in `agents/arc4/workflow.py` (~line 403-437) already has `resolved_goal_payload` in scope (set earlier in the same cycle, right after `resolve` returns) — no new phase-result plumbing needed, just read fields already sitting on the object already in scope at the call site.

`ResolvedGoal` (`agents/arc4/types.py`) already carries `grounding_gate_passed: bool` and `metadata: dict` (which `goal_resolver.py::resolve()` already populates with `hypotheses`, `llm_escalated`, `llm_reason`, `grounding_gate_passed`). Nothing new needs to be computed — this card is purely about threading already-computed data through, exactly like A230 did for the readiness report.

### Concrete design

1. **`workflow.py`'s normal-cycle Annatar call gains a new keyword argument**, e.g. `resolve_report=...`, built from `resolved_goal_payload` right before the call:
   ```python
   resolve_report = {
       "grounding_gate_passed": resolved_goal_payload.grounding_gate_passed,
       "llm_escalated": bool(resolved_goal_payload.metadata.get("llm_escalated")),
       "llm_reason": resolved_goal_payload.metadata.get("llm_reason"),
       "hypothesis_count": len(resolved_goal_payload.metadata.get("hypotheses", [])),
       "top_two_confidence_gap": _confidence_gap(resolved_goal_payload),  # see helper below
   }
   ```
   Where `_confidence_gap` is a small local helper (or inline) computing `selected.confidence - alternatives[0].confidence` when at least one alternative exists, else `None` — mirrors `goal_resolver.py::_should_escalate_to_llm`'s own `ambiguity_gap` computation, but read-only here (no new logic invented, just re-derived from data already on `ResolvedGoal`).
2. **`CycleSignals` gains new optional fields**: `resolve_grounding_gate_passed: bool | None = None`, `resolve_llm_escalated: bool | None = None`, `resolve_hypothesis_ambiguity: float | None = None` — mirror A230's `readiness_status`/`readiness_entities_mapped`/`readiness_entities_total` fields exactly (same docstring convention: "carries no decision weight for the per-anchor transition, but visible").
3. **`compute_cycle_signals`/`run_annatar_cycle`** (`annatar_signals.py`) accept the new `resolve_report` parameter, set the new `CycleSignals` fields from it. `transition()` itself is NOT modified in this card unless Track A below finds a real reason to (see below).
4. **`ports.py::AnnatarPhase`** and `arc_runtime/bundle.py`'s real `annatar` closure thread the new optional parameter through, same pattern as A230's `readiness_report`.

### Track A: should any of this actually influence `transition()`'s decision, or stay purely informational?

Investigate before deciding, don't presuppose:

- A217 already gives COMPLEX-domain anchors more deepening patience because live evidence *genuinely* disagrees. Is a goal-resolution-level ambiguity (`resolve_hypothesis_ambiguity` small) a similar, legitimate "give it more patience" signal, or a different kind of uncertainty that shouldn't affect anchor patience the same way? Read `annatar_state_machine.py::transition()`'s existing COMPLEX-domain branch (~line 203-216) before deciding whether resolve-level ambiguity deserves an analogous extension or should stay purely informational for now.
- If Track A concludes "informational only, for now" — that's a complete, acceptable outcome for this card (matches A230's own initial scope before any further extension was needed). Write the reasoning into the Outcome either way.

## Implementation approach

### Files

- Modify: `agents/arc4/workflow.py` — the normal-cycle Annatar call (~line 403-437), build and pass `resolve_report`.
- Modify: `agents/arc4/annatar_state_machine.py` — `CycleSignals` new fields.
- Modify: `agents/arc4/annatar_signals.py` — `compute_cycle_signals`/`run_annatar_cycle` accept and set the new fields.
- Modify: `agents/arc4/ports.py` — `AnnatarPhase` protocol signature.
- Modify: `arc_runtime/bundle.py` — real `annatar` closure threads the new parameter.
- Test: extend `tests/test_a202_annatar_orchestrator_integration.py` (or wherever A230's own `CycleSignals`/`AnnatarOutcome` field tests live — reuse that file, don't create a parallel one) and `tests/test_arc4_workflow.py`/`test_a224_workflow_readiness_integration.py`-adjacent tests for the normal-cycle path.

### TDD

- New test: `compute_cycle_signals`/`run_annatar_cycle` called with a `resolve_report` dict — confirm the resulting `CycleSignals`/output correctly carries the values through.
- Regression test: `transition()`'s output is unaffected by the new `CycleSignals` fields being set (unless Track A concluded otherwise, in which case test the new behavior explicitly instead).
- Regression: every existing `run_annatar_cycle`/`compute_cycle_signals` test continues to pass unchanged with no `resolve_report` argument (optional, defaults preserve current behavior).
- Integration: a `workflow.py`-level test confirming the normal-cycle Annatar call actually receives `resolve_report` built from a real `resolved_goal_payload` (e.g. `grounding_gate_passed=False` on the payload flows through to what the annatar dependency mock receives).

### Validation commands

```bash
.venv/bin/python -m pytest tests/test_a202_annatar_orchestrator_integration.py tests/test_a200_annatar_state_machine.py tests/test_arc4_workflow.py -v
make test-a
make test-all
```

### Live-verify

Same environment/discipline as A230's own verification. Add a targeted log line (mirroring A230's `PROBE_ANNATAR` precedent) at the normal-cycle Annatar call site logging `resolve_report`'s contents, run a live smoke, confirm real `grounding_gate_passed`/`llm_escalated` values actually flow through on a real episode — not just a passing unit test. Remove the debug log (or keep it as a permanent INFO line if it proves useful for future debugging, matching A230's own choice to keep `PROBE_ANNATAR` permanently) — decide during implementation.

## Assumptions/defaults

- This card threads data through; it does not change what `resolve()` itself computes (that's A171/A233's job) or what `transition()` decides (unless Track A finds a real reason, which should be argued explicitly, not assumed).
