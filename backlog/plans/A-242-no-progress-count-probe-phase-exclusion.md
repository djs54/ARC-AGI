# A242 — `consecutive_no_progress_count` Probe-Phase Exclusion: Plan

## Card metadata

- Card: `backlog/A242.md`
- Depends on: A230 (the exact scoping precedent this mirrors), A236 (the fix whose effect this masking has hidden in every live-verify so far), A224 (the probe-phase mechanism), A241 (the resume-mapping interaction to check)

## Design (mostly settled — mirror an existing precedent — with one real interaction to investigate)

Confirmed by direct read before writing this plan:

- `agents/arc4/workflow.py:180` — probe-path call to `self._record_evaluation_state(state, execution_payload, evaluation_payload)`, inside the `if probe_candidate is not None:` block.
- `agents/arc4/workflow.py:385` — the normal goal-directed cycle's call to the same method.
- `agents/arc4/workflow.py:743-751` — `_record_evaluation_state`'s body: resolves `action_key`, then calls `record_evaluation_outcome(no_progress_count=state.consecutive_no_progress_count, falsification_counts=state.action_falsification_counts, action_key=action_key, meaningful_progress=evaluation.meaningful_progress, falsification_delta=evaluation.falsification_delta)` and assigns the result back onto `state.consecutive_no_progress_count`.
- `agents/arc4/cycle_policy.py:64-74` — `record_evaluation_outcome`'s actual increment/reset logic (read this in full before designing the fix — confirm exactly what it does with `no_progress_count` besides `+ 1`, e.g. does it ever reset it, under what condition).
- `agents/arc4/goal_resolver.py:428,447` — the two `_should_escalate_to_llm` consumers.

### The fix

Mirror A230's own established precedent exactly (`annatar_unproductive_anchor_streak`'s scoping to `readiness_report is None` cycles in `annatar_signals.py::run_annatar_cycle`) rather than inventing a new mechanism. Concretely, investigate the cleanest way to make the probe-path call site at `workflow.py:180` not affect `state.consecutive_no_progress_count`:

- **Option A**: give `_record_evaluation_state` (or `record_evaluation_outcome`) a parameter (e.g. `count_toward_no_progress: bool = True`) that the probe-path call site sets `False`. Check what else `_record_evaluation_state`/`record_evaluation_outcome` does besides updating `consecutive_no_progress_count` (falsification counts, at minimum) — confirm those other updates still need to happen for probe cycles (they likely do — a probe click's falsification history is real and should still accumulate), so this can't be a blanket "skip the whole call for probes," only the one field.
- **Option B**: split `record_evaluation_outcome`'s no-progress-count update into its own smaller function, called only from the goal-directed call site, while `_record_evaluation_state` itself (still called from both places) only handles the falsification-count bookkeeping shared by both.

Check both against `cycle_policy.py`'s actual current shape before choosing — this is a small, low-risk function, read the whole thing rather than assuming from the two grepped line numbers above.

### The real thing to investigate: A241 interaction

A241 (already merged) can reset `state.readiness_gate_resolved = False` and re-enter the probe-path block *mid-episode*, after goal-directed play has already run for a while and accumulated some real `consecutive_no_progress_count` from genuine goal-directed no-progress. Investigate:

- Should a **resumed** probe window's cycles (via A241) also be excluded from the count, consistent with the original probe phase? (Almost certainly yes — a resumed probe cycle is exploratory in exactly the same sense as the original probe phase; there's no principled reason to treat it differently. But confirm this against the fix's actual mechanism, e.g. if Option A's `count_toward_no_progress` parameter is threaded through based on whether the call is inside the `if probe_candidate is not None:` block, this should already be automatically true for a resumed probe window too, since it re-enters the same code path — verify this is actually the case, don't just assume it falls out for free.)
- Should the **goal-directed** `consecutive_no_progress_count` accumulated *before* a resume be preserved, reset, or otherwise reconsidered once goal-directed play resumes *after* the remap? Read A241's actual reset behavior for `annatar_unproductive_anchor_streak` (it resets to 0 on resume, per `annatar_signals.py`'s override block) — decide whether `consecutive_no_progress_count` should follow the same reset-on-resume pattern, or whether it's a genuinely different signal that should keep accumulating goal-directed history across the remap (the streak and this counter answer different questions — the streak is about anchor productivity, this counter is about whether escalating to the LLM again is warranted — don't assume they should behave identically without checking each on its own terms).

## Implementation approach

### Files

- Modify: `agents/arc4/workflow.py` — `_record_evaluation_state`'s signature/call sites, per whichever option is chosen.
- Modify: `agents/arc4/cycle_policy.py` — only if Option B (splitting the function) is chosen.
- Test: new `tests/test_a242_no_progress_count_probe_exclusion.py`.

### TDD

- New test: a sequence of probe-path `_record_evaluation_state` calls (mirroring the probe-path call site's actual invocation shape) with `meaningful_progress=False` every time — confirm `state.consecutive_no_progress_count` stays at 0 (or whatever its pre-episode default is) throughout, not incrementing.
- New test: the same sequence via the goal-directed call site — confirm it still increments exactly as it does today (this is the regression guard: goal-directed no-progress tracking must be completely unaffected).
- New test: falsification counts (`state.action_falsification_counts`) still update correctly from probe-path calls even though `consecutive_no_progress_count` doesn't — confirms the fix didn't accidentally skip the whole call, only the one field.
- New test: a mixed sequence — probe cycles (no increment) followed by goal-directed cycles (real increment) — confirm the goal-directed cycles' count starts from a clean baseline unaffected by the preceding probe cycles, reproducing this card's own live evidence in a deterministic unit test.
- New test (A241 interaction): a resumed probe window's cycles (after a goal-directed `consecutive_no_progress_count` had already accumulated some real value) also don't increment the count — and assert whatever reset-on-resume decision Step 2's investigation settled on.
- Regression: `goal_resolver.py::_should_escalate_to_llm`'s existing tests (both branches) continue to pass unchanged.

### Validation commands

```bash
.venv/bin/python -m pytest tests/test_a242_no_progress_count_probe_exclusion.py -v
.venv/bin/python -m pytest tests/test_arc4_goal_resolver.py -v
make test-a
make test-all
```

### Live-verify

Same environment/discipline as every prior card this investigation (`.venv` worktree symlink if isolated, `CAMPY_MCP_CMD` absolute path, `campy start` + warm-up wait if the daemon shows offline, full `tee`'d output read completely). Run one or more live smokes and compare the resolve-phase LLM escalation rate (via `RESOLVE_ANNATAR llm_escalated=` log lines, or `scripts/graph_compliance_report.py`'s `llm_escalation_rate_goal_per_100`) against today's two measured baselines (TN36 15/15 = 100%, S5I5 9/9 = 100%) or a fresh comparable pair on similar-shaped puzzles. Puzzle assignment is random -- if the specific puzzles don't recur, report the real before/after numbers on whatever puzzles actually come up, honestly, rather than forcing a direct comparison that isn't really apples-to-apples. The acceptance bar is a measurable drop in `under_confident`-driven (not `ambiguous`-driven) escalations specifically -- if `top_two_confidence_gap` stays genuinely small (real ambiguity) on the cycles that still escalate, that's expected and correct, not a sign the fix didn't work.

## Assumptions/defaults

- Mirror A230's exact scoping pattern (non-probe cycles only) as the default shape -- this is a proven precedent in the same codebase, not a new design to validate from scratch.
- Falsification-count bookkeeping for probe-path calls is unaffected by this fix -- only `consecutive_no_progress_count` itself is scoped, unless investigation of `cycle_policy.py`'s actual shape shows a reason the two can't be cleanly separated.
