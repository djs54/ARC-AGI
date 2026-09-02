# A243 — Per-Goal Escalation Signal: Plan

## Card metadata

- Card: `backlog/A243.md`
- Depends on: A242 (sibling fix, probe-phase boundary), A230 (scoping precedent), A236 (patience-gate fix masked by this issue)

## Design (Track A concrete and low-risk; Track B genuinely open — investigate both, don't presuppose)

Confirmed by direct read before writing this plan:

- `agents/arc4/goal_resolver.py:428,447` — `_should_escalate_to_llm`'s two checks, both currently reading `state.consecutive_no_progress_count`.
- `agents/arc4/workflow.py:783-788` (inside `_record_evaluation_state`) — `state.goal_failure_counts[goal_id]` already correctly per-goal-scoped: resets to 0 on `meaningful_progress`, increments otherwise, keyed by `state.active_goal.selected.goal_id`.
- `agents/arc4/goal_resolver.py:518-537` (`_apply_failure_decay`) — the existing consumer of `goal_failure_counts`, for a related but distinct purpose (decaying a repeatedly-failed goal's confidence, not gating LLM escalation).
- Confirmed live (card's own evidence): `goal_failure_counts` naturally avoids A242's exact bug too, since the probe phase's sentinel goal_id (`"readiness_probe"`) never collides with a real goal's key.

### Track A: route `_should_escalate_to_llm` to `goal_failure_counts`

```python
def _should_escalate_to_llm(self, state: WorkflowState, hypotheses: Sequence[GoalHypothesis]) -> bool:
    if len(hypotheses) < 2:
        if not hypotheses:
            return False
        goal_failures = state.goal_failure_counts.get(hypotheses[0].goal_id, 0)
        return bool(hypotheses[0].confidence < self._limits.low_confidence_threshold and goal_failures >= self._limits.llm_patience_steps)

    ordered = self._order_hypotheses(hypotheses)
    top = ordered[0]
    runner_up = ordered[1]
    ambiguous = (top.confidence - runner_up.confidence) <= self._limits.ambiguity_gap
    goal_failures = state.goal_failure_counts.get(top.goal_id, 0)
    under_confident = top.confidence < self._limits.low_confidence_threshold and goal_failures >= self._limits.llm_patience_steps
    return ambiguous or under_confident
```

(Illustrative sketch, not a mandate — confirm the exact current shape of `_should_escalate_to_llm` before writing this, given A236's own reopened-fix history already touched this function's neighboring `ambiguous` branch; make sure this change composes cleanly with that existing logic, doesn't duplicate the streak-tracking machinery A236 added.)

**Two sub-questions to resolve with real investigation, not assumption:**

1. **Does `consecutive_no_progress_count` still have a legitimate role in this function, or should it be dropped entirely?** It currently answers "has the whole episode, across every goal, been unproductive" — which is a genuinely different question from "has *this* goal failed repeatedly." Consider: a puzzle where each individual goal only gets tried once or twice before Annatar moves on (so no single goal's `goal_failure_counts` ever crosses threshold) but the *episode* as a whole is clearly going nowhere — should that still escalate? Check whether this scenario actually occurs in live data, and if so, whether `annatar_unproductive_anchor_streak` (already tracking whole-episode unproductiveness, per A230/A235) already covers this case from a different angle, making a second whole-episode signal in `_should_escalate_to_llm` redundant. Don't keep `consecutive_no_progress_count` in this function just because removing it feels risky — check if its removal actually changes any live escalation outcome for the worse.
2. **Is `llm_patience_steps` (default 2) still the right threshold for a per-goal-scoped count?** The value was tuned (or at least chosen) for the flat, whole-episode counter's semantics. A per-goal counter may warrant a different value — check real live data (how many cycles does a goal typically get before Annatar moves on anyway, per `deepening_cycle_count`/`max_deepening_cycles_before_llm` in `annatar_state_machine.py`) rather than assuming 2 transfers cleanly.

### Track B: should this be graph-grounded instead of (or alongside) a local per-goal counter?

`goal_failure_counts` is correctly *scoped* but still local-process bookkeeping — not a graph consultation. The deeper question the operator raised: should "has this goal's hypothesis space been exhausted" be answered by real graph evidence (a confirmed-falsified rule/hypothesis state for the goal, mirroring `classify_entity_domain_detailed`'s CHAOTIC classification for entities) rather than a local failure tally, even a correctly-scoped one?

Investigate:
- Does `resolve()` already have relevant graph evidence in scope this same cycle from `_merge_graph_evidence`'s earlier fetch (the same zero-extra-round-trip reuse pattern A233 found for `_apply_grounding_gate`) — if so, a graph-grounded check might be nearly free to add, changing the cost/benefit calculus significantly. Check this before assuming it requires a new round trip.
- Whether a graph-grounded signal would actually produce different escalation decisions than `goal_failure_counts` alone on real live data — if the two signals agree in practice (a goal that's failed `N` local times also shows confirmed-negative graph evidence by then), building the graph-grounded version adds complexity without changing outcomes, and Track A alone is the right, complete fix (documented as a reasoned "Track B not warranted now," same standard as A233 Track B/A240's no-fix-needed outcomes). If they diverge in a way that matters (e.g., a goal gets graph-confirmed-dead in 1 local attempt but `goal_failure_counts` wouldn't cross `llm_patience_steps` until attempt 2), that's real evidence Track B is worth building.

## Implementation approach

### Files

- Modify: `agents/arc4/goal_resolver.py` — `_should_escalate_to_llm`, per Track A's chosen shape.
- Modify (only if Track B is warranted): `agents/arc4/goal_resolver.py`/`agents/arc4/graph_queries.py` — a graph-grounded exhaustion check, reusing already-fetched evidence where possible.
- Test: new `tests/test_a243_per_goal_escalation_signal.py`.

### TDD

- New test: a goal that fails twice under its own `goal_id`, then Annatar switches to a fresh, never-tried goal — confirm the fresh goal's first cycle does NOT escalate purely from inherited history (the exact live-observed bug), while the failed goal, if revisited later, correctly still shows its own real prior failure count.
- New test: a goal genuinely failing `llm_patience_steps` times in a row under its own `goal_id` still escalates — the regression guard for real stalled-goal detection.
- New test: reproduce this card's own live evidence deterministically — a sequence of goal switches (block-5, block-5, point-1, line-1, line-1, line-15×5) with no real progress on any of them, asserting each goal's *own* escalation decision is based only on its own accumulated failures, not the sum across all of them.
- Regression: `tests/test_arc4_goal_resolver.py`'s existing `_should_escalate_to_llm`-adjacent tests continue to pass, or are updated with a stated reason if their fixtures assumed the old flat-counter behavior.
- If Track B is implemented: a test showing the graph-grounded check firing on real (mocked) confirmed-negative evidence independent of local failure count.

### Validation commands

```bash
.venv/bin/python -m pytest tests/test_a243_per_goal_escalation_signal.py -v
.venv/bin/python -m pytest tests/test_arc4_goal_resolver.py -v
make test-a
make test-all
```

### Live-verify

Same environment/discipline as every prior card this investigation (`.venv` worktree symlink if isolated, `CAMPY_MCP_CMD` absolute path, `campy start` + warm-up wait if the daemon shows offline, full `tee`'d output read completely, generous timeout — recent runs in this session have taken 1-4 minutes). Run a live smoke on a puzzle where goal-directed play switches goals multiple times (common in this session's own recent runs) and confirm, via `STALL_CHECK`/a new log line if one is added, that a freshly-selected goal's escalation decision is no longer inflated by a prior unrelated goal's failure history. Report real before/after numbers on whatever puzzle actually comes up (assignment is random) — don't force a specific comparison.

## Assumptions/defaults

- Track A (reuse `goal_failure_counts`) is close to a settled direction given how directly it answers the card's own evidence — the open questions are refinements (does `consecutive_no_progress_count` still play any role, what threshold), not whether to do it at all.
- Track B is a genuine, uncommitted investigation — do not build a graph-grounded exhaustion check unless real data shows it would change actual escalation decisions Track A alone gets wrong.
