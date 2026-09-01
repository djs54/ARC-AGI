# A230 — Annatar Sees the Readiness/Probe Phase: Plan

## Card metadata

- Card: `backlog/A230.md`
- Depends on: A224 (readiness gate), A202/A207/A209/A212 (the informed-not-empowered precedent this follows)

## Design (settled here, not left open — implementer should build this, not re-derive it)

Confirmed by direct read before writing this plan:

- `run_annatar_cycle` (`agents/arc4/annatar_signals.py:352`) already prefers "the just-executed candidate's entity_ref if it has one" when picking a fresh anchor. A224 Task 4's `_select_readiness_probe` already stamps `entity_ref` onto its `PlanCandidate.metadata`. **This means routing a probe cycle through the existing `self._dependencies.annatar(...)` call requires no new anchor-selection logic — the probed entity naturally becomes the anchor**, and Annatar's existing per-anchor reasoning (`compute_cycle_signals`/`transition()` — is this entity CONVERGED/COMPLEX/CHAOTIC/DISORDER, meaningful_progress, etc.) applies to it exactly as it already does for any other click.
- `AnnatarDecision` (`ADVANCE`/`REPEAT_DEEPEN`/`REPEAT_RETRY`/`TERMINATE`) is per-anchor: "what to do about the entity/goal I'm currently investigating." It has no concept of "is the whole perception mapped yet" — that is a genuinely different, whole-episode-scoped question, not something to force into the per-anchor vocabulary.
- Therefore: routing probe cycles through Annatar's existing per-anchor machinery answers "what should happen to THIS probed entity" (already solved, reuse as-is) but does not by itself answer "should we keep probing OTHER entities, or is exploration complete." That second question needs a new, explicit field on `AnnatarOutcome`, computed by `run_annatar_cycle`'s own glue code (which already does whole-episode bookkeeping beyond pure per-anchor `transition()` — see `annatar_unproductive_anchor_streak`), not forced into `transition()`'s pure per-anchor state machine.

Concrete design:

1. **`workflow.py`'s probe-path block calls `self._dependencies.annatar(...)` after every probe cycle's `evaluate`** — the same call site the normal path already uses (`workflow.py:359`), not a new one. Pass the readiness-gate's report (`readiness_payload`'s `status`/`entities_mapped`/`entities_total`) through to this call via a new keyword argument, mirroring how `stall_reason`/`veto_reason` are already threaded through today.

2. **`CycleSignals` gains new, informational-only fields**: `readiness_status: ReadinessStatus | None = None`, `readiness_entities_mapped: int | None = None`, `readiness_entities_total: int | None = None`. Set by `compute_cycle_signals` from the new parameter, read by nothing inside `transition()` (same "carries no decision weight for the per-anchor transition" precedent `veto_reason`/`stall_reason` already establish — document this identically).

3. **`AnnatarOutcome` gains `exploration_complete: bool | None = None`** — `None` when no readiness report was passed this cycle (i.e., normal post-readiness-gate cycles, or no readiness gate configured at all); `True`/`False` set directly by `run_annatar_cycle`'s own glue code from the readiness report's `status` (`READY` or `PARTIAL_FALLTHROUGH` → `True`; `NOT_READY` → `False`) whenever a report was passed in. This is Annatar's own answer to "did all actions/entities get explored" — computed once, in Annatar's own module, from the reports it received, not recomputed or re-decided by `workflow.py`.

4. **`workflow.py`'s probe-path loop acts on `outcome.exploration_complete`, not on `readiness_status()`'s raw return value directly.** After calling Annatar: if `outcome.exploration_complete` is `True`, set `state.readiness_gate_resolved = True` and fall through to the normal path (replacing today's direct branch on `readiness_status`'s own status enum). If `False` (or the call wasn't configured to report it), continue probing. This is the actual authority transfer this card is about — `workflow.py` asks Annatar "are we done," it doesn't compute the answer itself and just tell Annatar afterward.

5. **No rival component.** `readiness_status()` (A224 Task 3, in `annatar_state_machine.py`) is UNCHANGED — same pure function, same signature, same thresholds. What changes is who calls it and who acts on it: today `workflow.py` calls it directly and branches on it directly; after this card, the readiness *report* (status/entities_mapped/entities_total, already computed by the `readiness_gate` dependency exactly as today) is handed to Annatar, and `run_annatar_cycle` is the one that turns that report into `exploration_complete`. Confirm explicitly in the PR's Graph-Engineering Review (Shift B section) that this is a relocation of authority over already-existing logic, not a new decision-making component.

## Implementation approach

### Files

- Modify: `agents/arc4/annatar_state_machine.py` — `CycleSignals` (new fields).
- Modify: `agents/arc4/types.py` — `AnnatarOutcome` (new `exploration_complete` field, `to_dict`/`from_dict` if present).
- Modify: `agents/arc4/annatar_signals.py` — `compute_cycle_signals` (accept + set the new `CycleSignals` fields), `run_annatar_cycle` (accept the readiness report as a new keyword parameter, compute `exploration_complete`, thread through to the returned `AnnatarOutcome`).
- Modify: `agents/arc4/ports.py` — `AnnatarPhase` protocol's `__call__` signature (new optional keyword parameter for the readiness report, matching how `stall_reason`/`veto_reason` are already optional keyword params there).
- Modify: `agents/arc4/workflow.py` — probe-path block: call `self._dependencies.annatar(...)` after each probe's evaluate (currently absent), passing the readiness report; branch on `outcome.exploration_complete` instead of `readiness_status`'s raw return value where the loop currently decides to keep probing vs. resolve.
- Modify: `arc_runtime/bundle.py` — the real `annatar` closure already wraps `run_annatar_cycle`; thread the new parameter through its lambda signature (mirrors how `stall_reason`/`veto_reason`/`veto_alternative_action_id` are already threaded).
- Test: extend `tests/test_a224_workflow_readiness_integration.py`'s `TestWorkflowReadinessGateRouting` class (it already owns this exact area) with new cases; extend `tests/test_a202_annatar_orchestrator_integration.py` or wherever `run_annatar_cycle`'s own unit tests live for the new `exploration_complete` computation.

### Step 1: read the current exact shapes before editing

Re-read `agents/arc4/annatar_signals.py::run_annatar_cycle`'s full body (352-end), `agents/arc4/workflow.py`'s probe-path block (~122-200) and the normal-path Annatar call (~333-381), and `agents/arc4/ports.py::AnnatarPhase`'s current signature. Line numbers in this plan are as of 2026-08-31 (post-A229) — confirm they still match before editing; if they've shifted, that's fine, the plan's *intent* is what matters.

### Step 2: TDD, `CycleSignals`/`AnnatarOutcome` new fields

Write failing tests first:
- `CycleSignals` accepts and stores `readiness_status`/`readiness_entities_mapped`/`readiness_entities_total`, all optional, default `None`.
- `AnnatarOutcome` accepts and stores `exploration_complete`, optional, default `None`.
- `transition()` behavior is provably unchanged by these new fields (a regression test: two `CycleSignals` instances differing only in the new readiness fields must produce the same `transition()` output for otherwise-identical inputs).

### Step 3: TDD, `compute_cycle_signals`/`run_annatar_cycle`

- `compute_cycle_signals` accepts a new optional parameter (name it clearly, e.g. `readiness_report: Mapping[str, Any] | None = None`) and sets the three new `CycleSignals` fields from it when present.
- `run_annatar_cycle` accepts the same parameter, passes it through to `compute_cycle_signals`, and computes `exploration_complete` on the returned `AnnatarOutcome`: `True` when `readiness_report["status"]` is `READY` or `PARTIAL_FALLTHROUGH`, `False` when `NOT_READY`, `None` when `readiness_report` is `None`.
- Test: a probe-cycle-shaped call (readiness_report present, status=NOT_READY) returns `exploration_complete=False` alongside whatever per-anchor decision the probed entity's own domain/progress produces.
- Test: a probe-cycle-shaped call with status=READY/PARTIAL_FALLTHROUGH returns `exploration_complete=True`.
- Test: a normal (non-probe) cycle call with no `readiness_report` returns `exploration_complete=None`, and every other existing `run_annatar_cycle` test still passes unchanged (no readiness_report argument needed by any existing caller/test — it's optional and defaults to not affecting anything).

### Step 4: `ports.py` and `bundle.py` threading

Add the optional keyword parameter to `AnnatarPhase.__call__`'s protocol signature (matching `stall_reason`'s existing optional-keyword pattern exactly). Update the real `annatar` lambda in `arc_runtime/bundle.py` to accept and forward it.

### Step 5: `workflow.py` — the actual authority transfer

In the probe-path block, after `evaluation_payload` is established (right before today's `current_observation = execution_payload.observation; continue`), insert a call to `self._dependencies.annatar(...)` if `self._dependencies.annatar is not None` — same shape as the normal path's own Annatar call (state, perception, execution, evaluation), passing the readiness report (`readiness_payload`, already in scope) as the new keyword argument. Store `outcome.degraded` into `state.annatar_degraded` (same as the normal path already does). Then:

- If `outcome.exploration_complete is True`: set `state.readiness_gate_resolved = True` (replacing today's direct set on `NOT_READY`-with-nothing-left-to-probe / `READY` / `PARTIAL_FALLTHROUGH` — this becomes Annatar's call now, not `readiness_status`'s raw output inspected directly by `workflow.py`). Do NOT `continue` in this branch — fall through so the SAME cycle can proceed into the normal resolve/plan/vet path immediately (avoids wasting a full extra cycle just to notice the gate resolved). Decide during implementation whether "fall through same-cycle" or "continue to next cycle, normal path picks up next time" is cleaner given the surrounding loop structure — either is acceptable as long as it's Annatar's outcome driving the choice, not a direct read of `readiness_status`'s enum.
- If `outcome.exploration_complete is False` (or `None`, meaning no Annatar configured — preserve today's exact behavior when `self._dependencies.annatar is None`, matching the backward-compatibility convention every other Annatar integration point in this file already follows): continue probing as today.
- If `outcome.decision == "terminate"`: end the episode via the same `_finish(..., TERMINATED, "annatar_exhausted", ...)` path the normal per-cycle Annatar call already uses — a probe-phase anchor going nowhere should be able to end the whole episode exactly like a normal-phase one already can, since it's now going through the identical decision call.

When `self._dependencies.annatar is None`: behavior must be byte-for-byte identical to pre-A230 (branch directly on `readiness_status`'s own return value, exactly as today) — this is the same backward-compatibility guarantee every other `if self._dependencies.annatar is not None:` branch in this file already provides, and the existing `TestWorkflowReadinessGateRouting::test_no_readiness_gate_configured_behaves_exactly_as_before`-style test pattern should be extended to cover "readiness gate configured, Annatar not configured" specifically (a case A224 never had to consider, since Annatar wasn't reachable from the probe path at all until this card).

### Step 6: regression + new integration tests

Extend `tests/test_a224_workflow_readiness_integration.py::TestWorkflowReadinessGateRouting`:
- New test: with both `readiness_gate` and `annatar` configured, a probe cycle calls `self._dependencies.annatar` (assert a call-count tracker sees exactly 1 call per probe cycle — today it sees 0, this is the core regression this card closes).
- New test: Annatar's `exploration_complete=True` response causes `state.readiness_gate_resolved` to become `True` and the loop to stop probing, even if `readiness_status()` itself would have said `NOT_READY` (proves Annatar's outcome, not the raw status, is what's driving the loop) — construct a fake `annatar` dependency in the test that returns `exploration_complete=True` regardless of input, and confirm the orchestrator respects it.
- New test: with `readiness_gate` configured but `annatar=None`, behavior is unchanged from pre-A230 (byte-for-byte, matching the existing `test_no_readiness_gate_configured_behaves_exactly_as_before` test's own rigor, applied to this new combination).
- Existing 9 tests in this file, and the 65-test targeted regression set from A226 (`test_a217_domain_aware_anchor_patience.py`, `test_a218_no_op_rule_signal_for_classify_domain.py`, `test_a220_plan_generator_domain_visibility.py`, `test_a224_cynefin_domain_scoring.py`, `test_a208_entity_neighborhood_hard_exclusion.py`, `test_a224_readiness_probe_selection.py`) must all still pass unchanged.

### Step 7: full suite + make test-a

```bash
.venv/bin/python -m pytest -q
make test-a
make test-all
```

### Step 8: live-verification

Same environment setup as every prior card this investigation (`.venv` worktree symlink if in an isolated worktree, `CAMPY_MCP_CMD` absolute path — see any of A225/A226/A228/A229's own plan files for the exact commands). Run a live smoke, then confirm from the exported trace (`agent_execution_trace.json`'s `phase_transition` snapshots) that an `annatar` phase transition now appears interleaved with `readiness_gate` transitions during the probe phase (currently: zero `annatar` transitions until after `readiness_gate_resolved` flips) — this is the concrete, observable proof the routing actually changed, not just a passing unit test.

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a224_workflow_readiness_integration.py -v
.venv/bin/python -m pytest tests/ -k "a217 or a218 or a220 or a224 or a208 or a202" -v
make test-a
make test-all
```

## Assumptions/defaults

- `exploration_complete=None` (not `False`) is the correct default for "no readiness report this cycle" — distinguishes "Annatar wasn't asked" from "Annatar said not yet," matching this codebase's existing convention of using `None` for "not applicable" rather than overloading `False`.
- The exact same-cycle-fall-through-vs-continue-to-next-cycle choice in Step 5 is left to the implementer's judgment given the real surrounding loop structure at implementation time — call it out explicitly in the PR description either way, since it's a real behavioral choice, not a cosmetic one.
