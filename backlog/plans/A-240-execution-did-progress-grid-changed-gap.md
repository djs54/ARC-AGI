# A240 — Execution `did_progress`/`grid_changed` Gap: Plan

## Card metadata

- Card: `backlog/A240.md`
- Depends on: A214 (audited the downstream overrides, not this root), A150 (`WEAK_PREDICTION_KINDS`, the reason this can't be a naive fix), A235 (the parallel-signal pattern to consider borrowing)

## Design (investigation-first — do not presuppose the fix, per the card's own explicit warning)

Confirmed by direct read before writing this plan:

- `arc_runtime/game_session.py::_compute_progress` (~lines 151-171): computes `grid_changed` and `did_progress` in the same function; `did_progress = bool(win or level_gain > 0)` never consults `grid_changed`.
- `agents/arc4/evaluator.py:59-80`: `exec_meta.get("grid_changed")` (execution-level, falls back to local `_grid_unchanged` computation if absent) feeds `grid_changed_flag`, which in turn feeds `observed_kind` (`"grid_change"` when `grid_changed_flag` is true and no level-gain/state-change applies) — this is a SEPARATE consultation of grid-change information than `meaningful_progress`'s seed (`execution.did_progress`, evaluator.py:61). These are two different signals computed from overlapping but not identical inputs.
- `agents/arc4/evaluator.py:14-18`: `WEAK_PREDICTION_KINDS = frozenset({"grid_change"})` — `observed_kind == "grid_change"` is explicitly treated as a weak/insufficient-alone signal for confirming a prediction, by design (A150).
- `agents/arc4/annatar_signals.py:207-210`: the stale comment claiming `execution.metadata` never carries `grid_changed`.

### Step 1: establish real frequency/impact with live trace evidence

Before deciding anything, gather real data (matching A214's own stated (and, per its own Outcome, only partially honored) discipline — actually do this, don't skip to design-intent reasoning):

- Run one or more live smokes (or reuse recent saved trace files from this session's scratchpad if they're still available and cover a range of puzzles) and, for each executed step, check: `execution.did_progress` (or the trace's `meaningful_progress` field) vs. `grid_changed`/`progress_class` in the same step's telemetry.
- Quantify: across N observed steps, how many show `grid_changed=True` (or `progress_class` indicating real grid movement) while `meaningful_progress=False`? Is this common (like the M0R0 example already found) or rare?
- Check whether `evaluator.py`'s `observed_kind`/`grid_changed_flag` machinery already gives any *other* consumer (Annatar, telemetry, plan_generator's cynefin_domain scoring, etc.) visibility into "the grid changed" independent of `meaningful_progress` — if so, name exactly which consumer and confirm it's actually reachable/used, not just theoretically present. `agents/arc4/annatar_signals.py:212`'s `execution_inconclusive = not bool(eval_meta.get("grid_changed", False)) and not meaningful_progress` is one confirmed consumer already reading `grid_changed` independently — check if this already provides adequate mitigation for at least the Annatar-facing half of this gap, narrowing what (if anything) still needs a new signal.

### Step 2: decide the fix shape, if any, based on Step 1's real findings

Three candidate outcomes, in order of increasing intervention — pick the smallest one the evidence actually supports:

1. **No fix needed.** If Step 1 shows the gap is either rare in practice or already adequately mitigated by existing `grid_changed`-aware consumers (e.g. `execution_inconclusive` above), document this precisely as the verdict — same "no fix warranted, argued from evidence" standard as A228/A233 Track B. Still fix the stale comment (Step 3) regardless.
2. **A new parallel signal**, mirroring A235's `graph_grew` pattern: something like `execution.grid_changed` (already effectively available, just needs a documented, named consumer) gets explicitly read by whichever specific place Step 1 identifies as actually needing "did the grid change" independent of `meaningful_progress`'s strict win/level-gain definition — added alongside, never replacing, the existing signal.
3. **Only if Step 1's evidence specifically argues for it**: loosen `did_progress` itself. This is the highest-risk option (interacts with A150's weak-prediction handling) and should be treated as a last resort, not a default — if chosen, it must come with an explicit, tested check that `WEAK_PREDICTION_KINDS`/A150's falsification-accumulation behavior for plain `grid_change`-predicted candidates is unaffected (a `grid_change` prediction confirmed only by a raw grid change must still not count as a strong confirmation for scoring purposes, even if `did_progress` itself becomes more permissive).

### Step 3: fix the stale comment (do this regardless of Step 2's outcome)

`agents/arc4/annatar_signals.py:207-210` currently reads:

```python
    # execution_inconclusive: no clear grid change and no explicit progress
    # signal. Read from evaluation.metadata["grid_changed"] -- evaluator.py
    # (agents/arc4/evaluator.py) is the sole owner of computing this flag
    # (it falls back to `not grid_unchanged` when nothing upstream reports
    # one explicitly) and always sets it on EvaluationResult.metadata.
    # execution.metadata is never populated with "grid_changed" by any
    # executor (confirmed by grepping the whole package) -- reading from
    # execution.metadata instead, as an earlier sketch of this function
    # assumed, would silently always read None and produce wrong signals.
```

Correct the false claim (execution.metadata *is* populated with `grid_changed` by the real production transport, `arc_runtime/game_session.py`, and `Executor._normalize_result` copies it through) while preserving the correct, still-true reasoning for why this function reads from `evaluation.metadata` specifically (it's the resolved, fallback-applied value — the right one to read regardless of whether execution-level data happens to be present).

## Implementation approach

### Files

- Investigate only (Step 1): no code changes, trace/log analysis against `artifacts/agent_execution_trace.json`/`submission_results_single.live.jsonl` from fresh or recent live-smoke runs.
- Modify (Step 3, always): `agents/arc4/annatar_signals.py` — comment correction only, no logic change.
- Modify (Step 2, only if Step 1's evidence warrants it): `arc_runtime/game_session.py` and/or `agents/arc4/evaluator.py` and/or `agents/arc4/annatar_signals.py`, per whichever of the three candidate outcomes the evidence supports.
- Test: only if Step 2 produces a code change — new `tests/test_a240_*.py`, plus an explicit A150 regression test per Step 2 option 3's requirement if that option is chosen.

### Validation commands

```bash
# Step 1 investigation — no fixed command, use whatever combination of a
# fresh live-smoke run and/or existing saved trace files answers the
# frequency/impact question with real numbers.
make test-a
make test-all
```

### Live-verify

Only required if Step 2 produces a code change. Same environment/discipline as every prior card this investigation (`.venv` worktree symlink if isolated, `CAMPY_MCP_CMD` absolute path, `campy start` + warm-up wait if the daemon shows offline, full `tee`'d output). If a fix lands, confirm on a fresh run that the new/corrected signal behaves as designed on a real step where the grid changes without a level gain — puzzle assignment is random, so document what was actually observed rather than forcing a specific puzzle shape.

## Assumptions/defaults

- If Step 1's evidence is genuinely ambiguous (some steps show the gap mattering, others don't, no clear pattern), default to Step 2 option 1 (no fix, document precisely) rather than building speculative machinery — matching this session's own repeated "don't build what the evidence doesn't clearly warrant" discipline (A228, A233 Track B, A236's Option 2).
- Do not implement Step 2 option 3 (loosening `did_progress` directly) unless Step 1's evidence specifically and clearly argues for it over option 2 — the default lean, absent strong evidence otherwise, is option 2 (a parallel signal) or option 1 (no fix), matching A235's own established precedent for this exact shape of problem.
