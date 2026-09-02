# A241 — Readiness Gate Remap on Total Goal-Directed Failure: Plan

## Card metadata

- Card: `backlog/A241.md`
- Depends on: A224 (readiness gate origin), A230 (Annatar-owns-the-decision precedent), A235 (the TERMINATE override this intercepts), A207 (why a second decision-owner is the wrong shape)

## Design (investigation-first — three real open questions, do not presuppose answers)

Confirmed by direct read before writing this plan — see the card's Problem/tension sections for the exact code and live evidence. This plan lays out the investigation steps in the order they should actually be done; do not jump to implementation before Step 1.

### Step 1: staleness check (do this first — it may shrink the scope)

By the time `run_annatar_cycle`'s whole-episode-futility override fires (`agents/arc4/annatar_signals.py:634-639`), goal-directed play has already run for a while, and `plan_generator.py::_build_candidates` calls `classify_entity_domain_detailed` on every `ACTION6` candidate's `entity_ref` (confirmed during the Plan Generation phase audit earlier this session). This means some of the entities `readiness_gate_entities_mapped` originally counted as "unmapped" (still `DISORDER`) may have *incidentally* been resolved by goal-directed play's own scoring calls, even without ever going through the dedicated probe path.

Investigate: at the moment whole-episode-futility fires, re-query the graph (or re-derive from state, if the domain classifications are cached anywhere reachable) for the CURRENT count of still-`DISORDER` entities, and compare it against the stale `state.readiness_gate_entities_mapped`/`entities_total` snapshot from when the probe phase ended. If the real remaining-unmapped count has shrunk substantially by termination time, that changes both whether remapping is worth doing at all, and how much budget it would actually need. Do this with a live smoke run and real data, not by reasoning about the code shape alone — matching this session's own standing discipline (see A214's Outcome for what happens when this step gets skipped: its conclusions had to be downgraded to "design-intent-consistent, not live-trace-verified").

### Step 2: where the decision lives (Shift B)

The whole-episode-futility override currently reads:

```python
if readiness_report is None:
    if anchor.get("any_progress"):
        state.annatar_unproductive_anchor_streak = 0
    else:
        state.annatar_unproductive_anchor_streak += 1
    if state.annatar_unproductive_anchor_streak >= max_unproductive_anchors:
        decision = AnnatarDecision.TERMINATE
```

This is the one and only place whole-episode `TERMINATE` gets decided (confirmed: `decision_for_state`'s docstring at `annatar_state_machine.py:354-355` states this explicitly). The fix must add a condition here, in this same function, not have `workflow.py` second-guess Annatar's answer afterward — that would recreate A207's `second_veto` anti-pattern.

Investigate the mechanical shape:

```python
if state.annatar_unproductive_anchor_streak >= max_unproductive_anchors:
    if state.readiness_gate_partial and not state.readiness_gate_remap_used:
        # real remaining-unmapped count, per Step 1's finding -- if Step 1
        # shows the gap has substantially closed already via incidental
        # goal-directed classification, this condition may need an
        # additional check here (e.g. only remap if N or more entities are
        # STILL genuinely DISORDER), not fire on the stale snapshot alone
        decision = AnnatarDecision.<TBD -- new value, or existing value + reason>
        state.readiness_gate_remap_used = True
    else:
        decision = AnnatarDecision.TERMINATE
```

Check `agents/arc4/annatar_state_machine.py`'s `AnnatarDecision` enum (`ADVANCE`/`REPEAT_DEEPEN`/`REPEAT_RETRY`/`TERMINATE`) and `AnnatarOutcome`'s shape (`agents/arc4/types.py` — check the exact fields) before deciding whether this needs a new enum value (e.g. `RESUME_MAPPING`) threaded through every place that currently switches on `AnnatarDecision`, or whether it's cleaner to keep the outcome shape unchanged and instead add a new boolean field to `AnnatarOutcome` (mirroring how `exploration_complete` already exists as a separate field alongside `decision`, not a `decision` value itself) that `workflow.py` checks specifically for this case. The `exploration_complete` precedent (A230) is worth reading closely — it may be the more consistent pattern to follow here, since it already represents "additional info about a readiness-adjacent question, distinct from the raw per-anchor decision."

### Step 3: how workflow.py actually resumes probing

`state.readiness_gate_resolved = False` alone does nothing useful on its own — the whole-episode-futility check happens deep inside the goal-directed cycle body (past `workflow.py:122`'s readiness-gate `if` block, which already ran and moved on for this cycle). Simply resetting the flag doesn't route control back to the probe loop mid-cycle.

Investigate the cleanest way to actually resume probing within `WorkflowOrchestrator.run`'s existing `while True:` structure:
- Does resetting `state.readiness_gate_resolved = False` and then `continue`-ing the outer `while True:` loop (skipping the rest of THIS cycle's goal-directed logic, letting the NEXT cycle's iteration naturally re-enter the `if ... and not state.readiness_gate_resolved:` block at the top) work cleanly, given the loop's existing structure? Trace through what state needs to be reset/preserved across that transition (e.g., does `state.active_investigation_anchor` need clearing so a stale goal-directed anchor doesn't leak into the resumed probe phase? Does `annatar_unproductive_anchor_streak` need resetting so the remap attempt doesn't immediately re-trigger the same override on its very next goal-directed cycle before it's had a chance to make progress?).
- Confirm this doesn't require duplicating the probe-path code that already exists in the `if probe_candidate is not None:` block (~workflow.py:138-261) — the goal is to route back INTO that existing code path, not build a second copy of it.

### Step 4: the remap bound (operator-refined 2026-09-02 — replaces the single-use-flag idea below)

**Use the real entity count, not an arbitrary attempt cap.** `entities_total` is fixed per puzzle and `entities_mapped` only grows monotonically (an entity, once resolved out of `DISORDER`, never reverts). That's itself sufficient as the bound: resume is only ever warranted while `entities_mapped < entities_total`. Once it reaches `entities_total` (full `READY`), a subsequent whole-episode-futility TERMINATE is real and must not be blocked — no synthetic "already tried once" flag needed. This is more Shift-C-consistent than an arbitrary counter: the graph's own real state bounds the decision, not a process-local attempt tally.

**The subtlety this creates — the actual hard part of this step:** `readiness_status()`'s `PARTIAL_FALLTHROUGH` condition is `step_index / max_cycles >= budget_fraction_before_fallthrough` (0.5). `step_index` only increases and `max_cycles` is fixed for the episode, so once this ratio crosses 0.5 it stays crossed for the rest of the episode. **If the resume path just resets `state.readiness_gate_resolved = False` and re-enters the probe loop unchanged, the very first re-check of `readiness_status()` will immediately return `PARTIAL_FALLTHROUGH` again — before a single additional entity gets probed.** The naive reset accomplishes nothing.

Investigate the cleanest fix for this specific interaction. Candidate directions, evaluate rather than presuppose:
- **Rebase the budget fraction on remaining budget, not total elapsed budget**, specifically for a resumed probe phase — e.g., compute the fraction against `(max_cycles - step_index_at_resume)` instead of the original `max_cycles`, so the resumed window gets its own fair fraction of what's left rather than being permanently past a stale threshold. Check whether `readiness_status()` itself should grow a new optional parameter for this, or whether the caller (`bundle.py`'s `_readiness_gate` closure) should pass a different `max_cycles` value when calling it during a resume.
- **Or**: since the bound is now `entities_mapped < entities_total` (a real, finite, monotonic signal) rather than an arbitrary attempt count, consider whether the elapsed-budget-fraction check should be skipped entirely during a resume and rely solely on the entity-count bound plus the episode's own overall step-budget exhaustion (which already exists as a separate, pre-existing terminal condition) to prevent runaway probing. This is simpler than rebasing the fraction, and may be the more defensible choice given the bound is already real and monotonic — but confirm it doesn't let a resume consume an unreasonable fraction of the *remaining* budget on mapping alone before checking.

Whichever direction is chosen, confirm against real step-budget arithmetic (a representative episode's actual numbers, not assumed) that a resume leaves a sane amount of budget for a second round of goal-directed play afterward, not the entire remainder spent on mapping alone.

<details>
<summary>Original single-use-flag idea (superseded, kept for context only — do not implement this instead of the above)</summary>

`state.readiness_gate_remap_used: bool = False` (or similarly named) was the original proposed shape — a single-use cap, matching this session's own bias toward the simplest signal that's still honest (A236's Option 1 precedent). Superseded because it's a weaker, less principled bound than the real `entities_mapped < entities_total` condition — an attempt-count flag would either (a) block a second legitimate resume when the first one made real partial progress but still didn't finish, or (b) need its own further tuning to decide how many attempts are enough, neither of which the entity-count bound requires.

</details>

## Implementation approach

### Files

- Modify: `agents/arc4/annatar_signals.py` — the whole-episode-futility override block (~line 634-639), per Step 2's chosen mechanism.
- Modify: `agents/arc4/annatar_state_machine.py` — only if Step 2 concludes a new `AnnatarDecision` value is needed; otherwise unchanged.
- Modify: `agents/arc4/types.py` — `WorkflowState` gains `readiness_gate_remap_used: bool = False` (+ `to_dict`/`from_dict`); `AnnatarOutcome` gains whatever new field/value Step 2 settles on.
- Modify: `agents/arc4/workflow.py` — the control-flow change from Step 3, wherever the whole-episode-futility TERMINATE is currently handled in the normal (non-probe) cycle path.
- Test: new `tests/test_a241_readiness_remap_on_total_failure.py`.

### TDD

- New test: a scenario where `entities_mapped < entities_total` (real, unmapped territory genuinely remains) and the whole-episode-futility streak crosses threshold — confirm the override produces the new resume-mapping outcome instead of `TERMINATE`.
- New test: the same streak-crossing scenario but `entities_mapped == entities_total` (fully mapped, nothing left) — confirm `TERMINATE` fires exactly as it does today, unblocked, completely unaffected by this card's change. This is the single most important regression guard in the whole card.
- New test: confirm a resumed probe phase's very first `readiness_status()` re-check does NOT immediately re-fire `PARTIAL_FALLTHROUGH` before at least attempting to map further entities — i.e. directly test whatever mechanism Step 4 settles on for the elapsed-budget-fraction interaction, with a state where `step_index/max_cycles` is already well past 0.5 at resume time.
- New test: confirm resume can legitimately happen more than once in the same episode if entities remain unmapped after a first resume attempt (no artificial attempt-count limit) — but eventually converges (either full mapping reached, or the episode's own overall step-budget exhaustion terminates it), i.e. it is not literally unbounded in the pathological case.
- New test (workflow-level integration): confirm `WorkflowOrchestrator.run` actually re-enters the probe-path code when the resume-mapping outcome is returned, using the real `if probe_candidate is not None:` block, not a duplicate.
- Regression: every existing `tests/test_a202_annatar_orchestrator_integration.py::TestRunAnnatarCycleWholeEpisodeFutility` test continues to pass unchanged (this class already owns the whole-episode-futility area).
- Regression: every existing `tests/test_a224_*.py` readiness-gate test continues to pass unchanged.

### Validation commands

```bash
.venv/bin/python -m pytest tests/test_a241_readiness_remap_on_total_failure.py -v
.venv/bin/python -m pytest tests/test_a202_annatar_orchestrator_integration.py -v
.venv/bin/python -m pytest tests/test_a224_*.py -v
make test-a
make test-all
```

### Live-verify

Same environment/discipline as every prior card this investigation (`.venv` worktree symlink if isolated, `CAMPY_MCP_CMD` absolute path, `campy start` + warm-up wait if the daemon shows offline, full `tee`'d output read completely, never truncated). Run one or more live smokes looking for a puzzle shaped like RE86 (busy grid, likely `PARTIAL_FALLTHROUGH`) that also happens to fail goal-directed play entirely within budget — puzzle assignment is random, so this exact scenario may not recur on demand. If it does: confirm via the log (a new permanent log line for this event, mirroring the `PROBE_ANNATAR`/`RESOLVE_ANNATAR`/`ANCHOR_PROGRESS` precedent already established by A230/A234/A235, is worth adding here too for the same "durable live-debuggability" reason) that the episode actually resumed probing, mapped further entities, and either succeeded or hit its remap cap and terminated for real. If it doesn't recur naturally, report that honestly and rely on the TDD + integration-test coverage as primary evidence, per this session's standing discipline against overclaiming live verification that didn't happen.

## Assumptions/defaults

- The remap bound is `entities_mapped < entities_total` (real, monotonic, graph-grounded), not an arbitrary attempt-count flag — this was an explicit operator correction to the plan's original design and supersedes it.
- Prefer extending `AnnatarOutcome` with a new field (mirroring `exploration_complete`'s existing precedent) over adding a new `AnnatarDecision` enum value, unless Step 2's investigation finds a concrete reason the enum approach fits the existing switch/dispatch code more cleanly — check both, don't assume.
- If Step 1's staleness check shows the gap has already substantially closed by the time termination fires on most real episodes, that's a legitimate basis for scoping this card down (e.g., only remap when the *current*, re-checked unmapped count is still above some meaningful threshold, not just whenever `readiness_gate_partial` was ever true) — follow the evidence, don't build the more elaborate version speculatively.
