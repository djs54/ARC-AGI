# A236 — Goal-Ambiguity LLM Escalation Patience: Plan

## Card metadata

- Card: `backlog/A236.md`
- Depends on: A234 (Shift-B boundary this must not reopen), A224 (`llm_patience_steps` precedent), A171/A233 (same file)

## Design (settled direction, one real investigation step before locking in)

Confirmed by direct read of `agents/arc4/goal_resolver.py::_should_escalate_to_llm` (lines 354-363) before writing this plan — see the card's Problem section for the exact code and live evidence (repeated identical `top_two_confidence_gap` values across consecutive cycles of the same anchor, 15/15 goal-directed cycles escalating in the `ls20-9607627b` live-smoke run).

### The fix (Option 1, default)

Add two new `WorkflowState` fields (`agents/arc4/types.py`, alongside `consecutive_no_progress_count` at line 433 — same section, same style):

```python
# A236: tracks how many consecutive cycles the SAME top-two goal_id pair
# has been ambiguous, so _should_escalate_to_llm's `ambiguous` branch can
# stop re-asking the LLM the identical question every cycle. Deliberately
# separate from consecutive_no_progress_count (a whole-episode "nothing
# has progressed" signal) -- conflating the two would treat "this specific
# pair is still ambiguous" and "the whole episode is stalled" as the same
# fact, which they are not.
last_ambiguous_pair: tuple[str, str] | None = None
ambiguous_pair_streak: int = 0
```

Add both to `to_dict()`/`from_dict()` (types.py ~line 523/554) following the exact existing pattern for `consecutive_no_progress_count`.

In `_should_escalate_to_llm` (goal_resolver.py:354), change the multi-hypothesis branch:

```python
ordered = self._order_hypotheses(hypotheses)
top = ordered[0]
runner_up = ordered[1]
pair = (top.goal_id, runner_up.goal_id)
ambiguous_raw = (top.confidence - runner_up.confidence) <= self._limits.ambiguity_gap
if ambiguous_raw and state.last_ambiguous_pair == pair:
    ambiguous = state.ambiguous_pair_streak < self._limits.llm_patience_steps
else:
    ambiguous = ambiguous_raw
under_confident = top.confidence < self._limits.low_confidence_threshold and state.consecutive_no_progress_count >= self._limits.llm_patience_steps
return ambiguous or under_confident
```

**Where `state.last_ambiguous_pair`/`state.ambiguous_pair_streak` actually get updated** — this must happen in `resolve()` itself (goal_resolver.py, after `_should_escalate_to_llm` is called, ~line 65), not inside the pure-predicate `_should_escalate_to_llm` (keep that function a pure decision, no side effects, matching its current shape):

```python
ordered_for_tracking = self._order_hypotheses(hypotheses)
if len(ordered_for_tracking) >= 2:
    pair = (ordered_for_tracking[0].goal_id, ordered_for_tracking[1].goal_id)
    if state.last_ambiguous_pair == pair:
        state.ambiguous_pair_streak += 1
    else:
        state.last_ambiguous_pair = pair
        state.ambiguous_pair_streak = 0
else:
    state.last_ambiguous_pair = None
    state.ambiguous_pair_streak = 0
```

Note this update must run **every cycle that computes ordered hypotheses**, not only when escalation actually fires — otherwise the streak can't track "how many consecutive cycles has this pair been ambiguous," only "how many times did we escalate." Place it right after the initial `hypotheses = self._merge_graph_evidence(...)` / before the `_should_escalate_to_llm` call so the streak reflects the pair *as it will be evaluated this cycle*, not a stale one from before this cycle's graph merge.

**Order-of-operations subtlety to verify during implementation:** `resolve()` re-orders hypotheses multiple times (`_order_hypotheses` called at least 3 times — before/after LLM merge, after failure decay). Confirm which specific ordering is the one `_should_escalate_to_llm` itself uses internally (it calls `_order_hypotheses` again on its own inputs) and make sure the streak-tracking code computes the pair identity from the *same* ordering `_should_escalate_to_llm` will see, not an earlier or later one — a mismatch here would make the streak track the wrong pair and either over- or under-suppress.

### Investigate before committing further: is Option 2 (graph-freshness) also needed?

After Option 1 lands and is live-verified, check the live-smoke escalation-rate delta (`scripts/graph_compliance_report.py`'s `llm_escalation_rate_goal_per_100`, before vs. after). If the rate drops close to the number of *distinct* ambiguous pairs actually seen (i.e., each distinct pair gets escalated roughly `llm_patience_steps` + 1 times total, not repeatedly forever), Option 1 alone is sufficient — do not build Option 2 speculatively (same standard as A233 Track B / A228's "no fix warranted" precedent). If a puzzle instead shows the SAME pair cycling in and out of the streak window (e.g. `ambiguous_raw` flips False then True again for the identical pair as confidences jitter slightly cycle to cycle, resetting the streak each time without ever accumulating real patience), that is evidence Option 2's graph-freshness check is needed as a supplement — document that finding precisely in the card's Outcome rather than building it preemptively.

## Implementation approach

### Files

- Modify: `agents/arc4/types.py` — `WorkflowState` gains `last_ambiguous_pair`/`ambiguous_pair_streak`, `to_dict()`/`from_dict()` updated.
- Modify: `agents/arc4/goal_resolver.py` — `_should_escalate_to_llm`'s `ambiguous` branch gains the streak check; `resolve()` gains the streak-update block.
- Test: new `tests/test_a236_ambiguity_escalation_patience.py`.

### TDD

- New test: two consecutive `resolve()` calls with the identical top-two hypothesis pair (same `goal_id`s, confidences within `ambiguity_gap` both times) — confirm the LLM is queried the first time but NOT the second (within `llm_patience_steps`), and IS queried again once the streak crosses `llm_patience_steps`.
- New test: two consecutive `resolve()` calls where the pair *changes* (a different runner-up `goal_id` the second time, or the gap closes further with a third hypothesis now the runner-up) — confirm the LLM is queried both times (genuinely new ambiguity is never suppressed).
- New test: a pair that goes ambiguous, then unambiguous (confidences diverge past `ambiguity_gap`), then ambiguous again — confirm the streak resets on the unambiguous cycle in between, so re-ambiguity after a real gap is treated as new, not still-suppressed.
- Regression test: the `under_confident` branch's own existing `llm_patience_steps` gate is completely unaffected (still reads `consecutive_no_progress_count`, not the new fields) — assert via an existing or lightly-extended fixture that low-confidence-with-no-progress escalation behavior is byte-for-byte unchanged.
- Regression: every existing test in `tests/test_arc4_goal_resolver.py` and `tests/test_a233_*.py`/`tests/test_a171_*.py` (if any) continues to pass unchanged unless the new fields' defaults (`None`/`0`) require a stated, reasoned fixture update.

### Validation commands

```bash
.venv/bin/python -m pytest tests/test_a236_ambiguity_escalation_patience.py -v
.venv/bin/python -m pytest tests/test_arc4_goal_resolver.py -v
make test-a
make test-all
```

### Live-verify

Same environment/discipline as every prior card this investigation (`.venv` worktree symlink if isolated, `CAMPY_MCP_CMD` absolute path, start the hippocampy brain daemon first via `campy start` if a readiness probe fails with "Brain: OFFLINE"). Run a live smoke on a puzzle likely to produce a persistently ambiguous goal pair (a puzzle similar in shape to `ls20-9607627b` — many entities, a stalled goal with a close runner-up) and confirm, via the `RESOLVE_ANNATAR` log line (already permanent from A234) plus `scripts/graph_compliance_report.py`, that:
1. The first cycle a pair goes ambiguous, `llm_escalated=True` fires (unchanged responsiveness).
2. Subsequent cycles with the identical pair do NOT re-escalate until the patience threshold elapses (`llm_escalated=False`, `top_two_confidence_gap` still small).
3. `llm_escalation_rate_goal_per_100` measurably drops versus a comparable pre-fix run, without any genuinely new ambiguity (different pair, or the same pair after a real confidence change) failing to escalate.

## Assumptions/defaults

- Option 1 (local per-pair streak) is a complete, acceptable outcome for this card if live data doesn't show a need for Option 2 — don't feel obligated to build the graph-freshness check speculatively.
- `llm_patience_steps` (existing default: 2) is reused as the patience threshold for the new streak rather than inventing a separate constant, unless investigation shows the two cases (whole-episode no-progress vs. single-pair ambiguity persistence) actually warrant different tuning — if so, add a new `GoalResolverLimits` field and say why in the Outcome.
