# A249 — Route `action_space_exhausted` Through Annatar Instead of Independent Termination: Plan

## Card metadata

- Card: `backlog/A249.md`
- Depends on: A202 (`stall_reason`'s exact treatment this mirrors), A194 (the graph-aware detection this card doesn't touch), A221 (the identical-bug-class precedent), A230 (`annatar_unproductive_anchor_streak`, the backstop this leans on)

## Design (confirmed by direct read before writing this plan)

- `agents/arc4/evaluator.py:149-158` — `decision`/`reason` assignment; `elif action_space_exhausted: decision = WorkflowDecision.TERMINATE; reason = "action_space_exhausted"` is the line to change.
- `agents/arc4/evaluator.py:234` — `PhaseStatus.TERMINATE if decision == WorkflowDecision.TERMINATE else PhaseStatus.OK` — automatically follows from the `decision` change above, no separate edit needed here.
- `agents/arc4/evaluator.py:365-386` — `_action_space_exhausted`'s own three-branch detection (`env_reported`/`graph_confirmed_no_untested`/`threshold_only`) — **do not change this function's own logic**, only what the caller does with its result.
- `agents/arc4/workflow.py:427-439` (line numbers as of A248's merge — confirm current numbers before editing, this function has been touched by nearly every recent card) — the `termination_from_evaluation`/`PhaseStatus.TERMINATE` short-circuit.
- `agents/arc4/cycle_policy.py:126-129` — `termination_from_evaluation(decision, reason)`, checks `decision == "terminate"` generically. Once `action_space_exhausted` no longer sets `decision=TERMINATE`, this function needs no change — it will simply never see that case again.
- `agents/arc4/annatar_signals.py:172,251,422,584` — `stall_reason` parameter (in both `compute_cycle_signals` and `run_annatar_cycle`), and the override block: `if stall_reason is not None: all_falsified = True; untested_remaining = False`.
- `agents/arc4/temporal_workflows.py` — mirror of `workflow.py`'s logic (already confirmed by A248 to independently reimplement `check_stall`'s call; check whether it has its own `termination_from_evaluation`/Annatar-equivalent short-circuit that needs the same fix — Temporal workflows may not have an Annatar dependency at all; confirm before assuming symmetry).

### The fix

**Step 1 — evaluator.py: stop producing TERMINATE for `action_space_exhausted`.**

```python
if terminal_reason is not None:
    decision = WorkflowDecision.TERMINATE
    reason = terminal_reason
elif action_space_exhausted:
    # A249: no longer TERMINATE here -- this signal is not environment-
    # authoritative (see backlog/A249.md) and now flows to Annatar via
    # workflow.py's stall_reason-equivalent channel instead of bypassing
    # it. decision stays CONTINUE; exhaustion_source/action_space_exhausted
    # remain in metadata unchanged so nothing downstream loses visibility.
    reason = "action_space_exhausted"
elif meaningful_progress:
    ...
```

(Illustrative — confirm the exact surrounding `if/elif` chain shape before editing; `reason` still needs *some* value here so existing consumers of `EvaluationResult.reason` don't regress — check what reads `evaluation.reason`/`evaluation_payload.reason` elsewhere, e.g. telemetry, to confirm a non-TERMINATE `reason="action_space_exhausted"` doesn't break anything that currently assumes TERMINATE-only reasons.)

**Step 2 — workflow.py: extract the signal and thread it to Annatar.**

At each `self._dependencies.annatar(...)` call site (grep `dependencies.annatar` in `workflow.py` to enumerate all of them — confirmed multiple exist, don't assume just one), pass `evaluation_payload.metadata.get("action_space_exhausted", False)` through to wherever `stall_reason` is already passed, e.g.:

```python
outcome = self._dependencies.annatar(
    ...,
    stall_reason=stall_reason,
    action_space_exhausted=evaluation_payload.metadata.get("action_space_exhausted", False),
    ...,
)
```

(Illustrative signature — decide whether to add a new `action_space_exhausted: bool` parameter throughout `annatar_signals.py`'s `compute_cycle_signals`/`run_annatar_cycle`, or to fold it at the workflow.py call site by computing an OR: `effective_stall_reason = stall_reason or ("action_space_exhausted" if evaluation_payload.metadata.get("action_space_exhausted") else None)` and passing that single value through unchanged. The latter is less invasive — fewer signatures to touch — but blurs two distinct reasons into one string; the former is more explicit but touches more call sites. Default lean: the OR-into-`stall_reason` approach, since `stall_reason`'s only current consumer (`annatar_signals.py:251`) treats it as a boolean presence check (`is not None`), not by its string value — confirm this is really true by reading every place `stall_reason`'s *value* (not just presence) is read before committing to this shortcut.)

**Step 3 — annatar_signals.py: no change needed if Step 2 uses the OR-into-`stall_reason` approach** (the existing `if stall_reason is not None:` override block already does the right thing). If Step 2 instead added a new parameter, extend the override condition: `if stall_reason is not None or action_space_exhausted:`.

**Step 4 — the `"env_reported"` question.** Before finalizing, run:

```bash
grep -rn "action_space_exhausted\|exhausted_action_space" agents/arc4/executor.py arc_runtime/*.py benchmarks/arc3/ sidequest_mcp_client/ 2>/dev/null
```

If this confirms (as this card's own preliminary search found) that nothing in this repo ever produces these metadata keys, no special-casing is needed — `env_reported`'s branch is dead code today and folding it in with the other two sources (per Step 1/2 above) is correct and simplest. If a real producer is found anywhere, that specific branch should instead be treated like `terminal_reason` (added to the *actual* environment-authoritative check, not this card's Annatar-routing fix) — document precisely which case applies, with the grep output, in `backlog/A249.md`'s Outcome.

**Step 5 — temporal_workflows.py.** Check whether this file's `ArcPuzzleWorkflow.run()` has any Annatar-equivalent dependency at all (A248's investigation already read this file closely — build on that, don't re-derive from scratch) or whether it's a simpler orchestrator without a Reasoner phase. If it has no Annatar equivalent, `action_space_exhausted`'s TERMINATE-avoidance in evaluator.py still applies (evaluator.py is shared), but there's nothing to route the signal *to* there — confirm what the Temporal path's own `termination_from_evaluation`-equivalent check does now that `action_space_exhausted` no longer produces `decision=TERMINATE`, and whether that changes Temporal-path behavior in a way that needs its own handling (e.g., does the Temporal loop need its own `action_space_exhausted`-driven continuation logic, or was it already relying on `evaluate()`'s TERMINATE as its only signal, meaning it would now silently continue forever on a genuinely exhausted action space with no Annatar to catch it via `annatar_unproductive_anchor_streak`). This is a real question to resolve with evidence, not assume symmetric with the inline orchestrator.

## Implementation approach

### Files

- Modify: `agents/arc4/evaluator.py` — the `decision`/`reason` assignment for `action_space_exhausted`.
- Modify: `agents/arc4/workflow.py` — every `self._dependencies.annatar(...)` call site.
- Modify (if the new-parameter approach is chosen): `agents/arc4/annatar_signals.py` — `compute_cycle_signals`/`run_annatar_cycle` signatures and the override block.
- Check and, if needed, modify: `agents/arc4/temporal_workflows.py`.
- Test: new `tests/test_a249_action_space_exhausted_through_annatar.py`.

### TDD

- New test: `action_space_exhausted=True` (via a fake execution/evaluation shape) with Annatar configured — confirm the episode does NOT terminate; confirm Annatar's `transition()` receives the override (`all_falsified=True`/`untested_remaining=False` or equivalent) and produces `EXHAUSTED`→`ADVANCE` for the current anchor, picking a fresh one, episode continues.
- New test: genuine `terminal_reason` (e.g., a fake WIN observation) — confirm the episode still terminates immediately and unconditionally, completely unaffected by this change. This is the single most important regression guard — write it first, confirm it passes against the CURRENT (pre-fix) code too, so you know it's testing the right thing before you change anything.
- New test: `action_space_exhausted=True` with Annatar NOT configured (legacy fallback, `self._dependencies.annatar is None`) — confirm behavior matches whatever this card's Step 5/legacy-path investigation determines is correct (either unchanged old behavior, per A221's own precedent for a genuinely separate consumer, or a documented reason it can't stay unchanged).
- New test: `EvaluationResult.reason == "action_space_exhausted"` and `metadata["action_space_exhausted"]`/`["exhaustion_source"]` are still populated exactly as before, even though `decision` is now `CONTINUE` — confirms visibility is preserved even though the consequence changed.
- New test: an episode that genuinely exhausts across many anchors still ends via `annatar_unproductive_anchor_streak`'s existing whole-episode-futility mechanism — confirms this card doesn't remove the ability to end a truly-exhausted episode, only relocates the decision.
- Regression: every existing `tests/test_a194_*.py`, `tests/test_a202_*.py`, `tests/test_a221_*.py`/`test_a200_annatar_state_machine.py` (for the `stall_reason`/`all_falsified` override), and any other `action_space_exhausted`/`termination_from_evaluation`-adjacent test continues to pass — find them via `grep -rl "action_space_exhausted\|termination_from_evaluation" tests/`.

### Validation commands

```bash
.venv/bin/python -m pytest tests/test_a249_action_space_exhausted_through_annatar.py -v
.venv/bin/python -m pytest tests/test_a194_graph_driven_termination.py tests/test_a202_annatar_orchestrator_integration.py -v
make test-a
make test-all
make check-compliance
```

### Live-verify

Same environment/discipline as every prior card this investigation (`CAMPY_MCP_CMD` pointing at the sibling `hippocampy` repo, `campy status` check first, full `tee`'d output to a log file read completely, generous timeout — `run_in_background: true` with `timeout: 600000`+ for anything beyond ~30 steps). This specific scenario (one action family retried past threshold, in a puzzle where other genuinely-untested actions/anchors still exist) may or may not occur in a randomly-assigned puzzle within a reasonable step budget — report honestly either way. If it doesn't recur, the TDD suite (which directly reproduces the evaluator-level scenario deterministically) is the primary evidence, same authorized fallback standard as A237/A244/A248.

## Assumptions/defaults

- Default to the OR-into-`stall_reason` approach (Step 2) unless investigation of `stall_reason`'s other consumers shows its string *value* (not just presence) is read somewhere this would break.
- Default to folding `env_reported` in with the other two sources (no special-casing) unless a real producer is found somewhere in the repo — confirm with the grep in Step 4, don't assume either way.
- If `temporal_workflows.py` has no Annatar-equivalent phase, do not invent one for this card — document the resulting behavior change (or lack of one) precisely, and if it introduces a real regression risk (e.g., the Temporal path silently losing its only exhaustion-termination signal with nothing to replace it), name that explicitly as a follow-up rather than solving it unscoped inside this card.
