# A246 — New-Anchor Selection Readiness Context: Plan

## Card metadata

- Card: `backlog/A246.md`
- Depends on: A230 (`readiness_report is None` convention, the signal this fix reuses), A224 (the phase boundary this bug re-blurs), A245 (companion finding, different mechanism, no hard dependency either direction)

## Design (narrow, well-precedented — one real sub-question to check)

Confirmed by direct read before writing this plan — see the card's Problem section for the exact code (`annatar_signals.py::run_annatar_cycle`'s `if anchor is None:` block, ~line 500-507) and live evidence (SK48 run, anchor 32 appearing 25s before `READINESS_REMAP`, ruling out A241's resume mechanism as the explanation).

### The fix

```python
if anchor is None:
    cand_meta = execution.candidate.metadata if execution.candidate is not None else {}
    entity_ref = cand_meta.get("entity_ref") if isinstance(cand_meta, dict) else None
    # A246: during goal-directed play (readiness_report is None, the same
    # established convention this function already uses elsewhere -- see
    # A230), prefer the active goal over an incidentally-entity_ref-carrying
    # candidate. Probe-phase anchor creation (readiness_report is not None)
    # is unchanged -- entity-preferring there is correct and load-bearing
    # for A224/A230/A231's own mapping behavior.
    active_goal_id = state.active_goal.selected.goal_id if state.active_goal is not None else None
    if readiness_report is None and active_goal_id is not None:
        anchor_ref, anchor_type = active_goal_id, "goal"
    elif entity_ref is not None:
        anchor_ref, anchor_type = entity_ref, "entity"
    else:
        anchor_ref, anchor_type = active_goal_id, "goal"
```

(Illustrative — confirm the exact current shape of this block before writing the real diff, and confirm `readiness_report` is genuinely in scope at this exact point in the function, not just somewhere else in it.)

### The one real sub-question: unconditional goal-preference, or a narrower rule?

The card names this as worth checking against live data rather than assuming: is it ever correct, during goal-directed play, to anchor on the entity a just-executed click targeted rather than the active goal — e.g., when that entity is itself central to the active goal's own evidence (a click that's part of actively testing the goal, where the entity IS effectively what the goal is about)? Investigate:
- Does `state.active_goal.selected.evidence` or `.metadata` ever reference the same `entity_ref` the candidate carried — if so, an entity-anchor pick in that specific case might actually be *more* correct than a blanket goal-anchor, since it's the same underlying investigation at a finer grain.
- Check this against live data (a few live-smoke runs) before deciding: does the simple "always prefer goal during goal-directed play" rule ever visibly discard a case where the entity-anchor pick would clearly have been better? If not found in live data, ship the simple rule — don't build the narrower evidence-cross-reference version speculatively.

Default lean, absent evidence otherwise: the simple unconditional rule above.

## Implementation approach

### Files

- Modify: `agents/arc4/annatar_signals.py` — the anchor-creation block inside `run_annatar_cycle`.
- Test: new `tests/test_a246_anchor_selection_readiness_context.py`.

### TDD

- New test: goal-directed cycle (`readiness_report=None`), just-executed candidate carries a real `entity_ref`, `state.active_goal` is set — confirm the new anchor is `anchor_type="goal"` using the active goal's `goal_id`, not the entity.
- New test: probe cycle (`readiness_report={...}` non-None), same candidate shape — confirm the new anchor is still `anchor_type="entity"`, completely unchanged from today's behavior (the critical regression guard).
- New test: goal-directed cycle, `state.active_goal is None` (edge case) — confirm a sane fallback (either the entity if present, or a defensible default), no crash.
- New test: goal-directed cycle, candidate carries no `entity_ref` at all (a non-click action) — confirm the existing else-branch goal-anchoring behavior is unchanged (this path already worked correctly before this card; verify it still does).
- Regression: every existing `tests/test_a224_*.py`/`tests/test_a230_*.py`/`tests/test_a231_*.py`/`tests/test_a202_annatar_orchestrator_integration.py` test continues to pass unchanged.

### Validation commands

```bash
.venv/bin/python -m pytest tests/test_a246_anchor_selection_readiness_context.py -v
.venv/bin/python -m pytest tests/test_a202_annatar_orchestrator_integration.py -v
make test-a
make test-all
```

### Live-verify

Same environment/discipline as every prior card this investigation (`.venv` worktree symlink if isolated, `CAMPY_MCP_CMD` absolute path, `campy start` + warm-up wait if the daemon shows offline, full `tee`'d output read completely, generous timeout — recent runs have taken 2-4+ minutes). Run a live smoke and confirm, via `ANCHOR_PROGRESS`'s existing log line, that once goal-directed play begins, a concluding goal-type anchor's next anchor is also goal-type (not an incidental entity pick) unless there's genuinely no active goal — puzzle assignment is random, report honestly what was actually observed. This also directly sets up cleaner conditions for A245's own live-verification, if that card is implemented afterward.

## Assumptions/defaults

- The simple, unconditional "prefer goal during goal-directed play" rule is the default unless live data specifically shows a case where the entity-anchor pick would clearly have been better.
- Do not change probe-phase behavior in any way — that logic is correct and load-bearing.
