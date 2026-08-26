# Plan: A215 — Audit: Does `goal_resolver.py` Need a Graph-Driven Exclusion Path?

## Card metadata

- ID: A215
- Priority: P2
- Layer: ARC runtime
- Dependencies: None (references A199, A208)

## Summary

`plan_generator.py` has real graph-driven exclusion (A208); `goal_resolver.py` only ever boosts confidence from graph evidence, never demotes/excludes. The one live goal-level graph signal (`VictoryCondition.confidence`, kept current every cycle via `record_evaluation` → `arc_update_goal_confidence`) is read back by `goal_resolver.py` but merged via `confidence=max(hypothesis.confidence, boost)` (`goal_resolver.py:304`) — structurally one-directional, so a low graph confidence can never pull a hypothesis's score down. Audit whether this asymmetry is a real, uncovered gap or whether local state (`_apply_grounding_gate`/`_apply_failure_decay`) already handles it.

**This card is written to be executable as three parallel, independently-dispatchable tracks.** If executing via subagent fan-out:
- Track A and Track B are read-only investigation agents that report their findings back to the primary session (no branch, no commit) — the primary session synthesizes both into the card's Outcome section itself. Do not have either track-agent write the Synthesis section, since it needs both tracks' results.
- Track C is independent, unconditional implementation work (new KPI telemetry) that does not depend on Tracks A/B's conclusions — dispatch it on its own branch, mirroring how A213/A214 were fanned out as separate parallel branches. It can be reviewed and merged on its own timeline, separate from whatever Tracks A/B's synthesis produces.

## Track A: is the graph-side signal itself trustworthy?

### Scope

Read-only investigation. Touches `agents/arc4/graph_queries.py` (ARC-side client) and the sibling repo at `/Users/djshelton/Desktop/GitProjects/hippocampy/campy/brain/thalamus/tools/arc_queries.py` (read-only — do not modify the sibling repo). No code changes in this track.

### Steps

1. Read `agents/arc4/graph_queries.py::record_evaluation` in full (search for `def record_evaluation`, currently around line 590). Confirm exactly what triggers the `update_goal_confidence` call, what `goal_id`/`confidence`/`has_meaningful_progress` values it sends, and whether this call is genuinely unconditional (fires every cycle regardless of grid change) — cross-reference against A213/A214's prior finding that this method fires unconditionally, but verify it yourself against the current code rather than trusting that summary.

2. Read hippocampy's `arc_update_goal_confidence` (search for `async def arc_update_goal_confidence` in `/Users/djshelton/Desktop/GitProjects/hippocampy/campy/brain/thalamus/tools/arc_queries.py`, starts around line 635) **in full — the visible portion during this card's authoring stopped partway through**. Determine precisely:
   - Does it SET `vc.confidence` to the incoming `new_confidence` value directly (a ratchet-free assignment), or does it only update under certain conditions (e.g. only if the new value is higher, mirroring the same one-directional pattern found on the ARC side)?
   - Does `has_meaningful_progress=False` ever cause `vc.confidence` to genuinely decrease over repeated calls, or does the handler ignore that flag / use it only for a different purpose?
   - If the write itself is already one-directional (boost-only) server-side, the ARC-side `max()` in `_merge_single_record` is redundant with an existing hippocampy-side limitation, not the sole source of the asymmetry — this changes where a fix (if any) belongs.

3. Read hippocampy's `arc_get_goal_evidence` (`arc_queries.py:497-523`) in full — already partially read during this card's scoping, confirm independently. Confirm the exact Cypher query and what `vc.confidence` reflects at read time (is there any staleness/caching, or is it a live read every call?).

4. Trace the `goal_id` consistency question: in `agents/arc4/graph_queries.py::record_evaluation`, what exact metadata key supplies `goal_id` (`metadata.get("goal_id") or metadata.get("resolved_goal_id")` — confirm the exact fallback chain in the current code)? Then trace backward: where does `evaluation.metadata` actually get `goal_id`/`resolved_goal_id` populated — read `agents/arc4/evaluator.py`'s `evaluate()` method and its `metadata_dict` construction (search for where `EvaluationResult`'s metadata dict is built) to confirm which key it actually sets, and whether it's guaranteed to be the SAME goal_id string `_merge_single_record` looks up via `record.get("goal_id")` from the READ side (`hypothesis.goal_id == goal_id` comparison in `goal_resolver.py:298`). A mismatch here (e.g. one side sends a slugified goal_id, the other expects the raw form, or one is `None` in practice) would make this whole mechanism silently inert — confirm this doesn't happen, with actual evidence (grep for both write-side and read-side key names, do not assume they match just because the field names look similar in isolation).

5. Write up Track A's finding as a self-contained block of prose (2-4 paragraphs), citing exact file:line references for every claim, ready to be pasted into `backlog/A215.md`'s Outcome section by the synthesizing session. Do NOT edit `backlog/A215.md` yourself if working as a fanned-out track-agent — report your finding back to the primary session instead (see "Reporting back" below).

## Track B: does local state already cover the gap in practice?

### Scope

Investigation using real trace evidence where possible, touching `agents/arc4/goal_resolver.py`, `agents/arc4/types.py` (for `WorkflowState.goal_failure_counts`'s write site), and `artifacts/agent_execution_trace.json` or a fresh `make smoke` run. No code changes in this track.

### Steps

1. Read `goal_resolver.py::_apply_grounding_gate` (currently ~lines 435-471) and `_apply_failure_decay` (currently ~lines 404-433) in full. For each, write down in plain terms: exactly what state must be true for it to fire, and what it does when it fires (clamp to a ceiling vs. multiplicative decay).

2. Find where `state.goal_failure_counts` is actually incremented — grep `agents/arc4/*.py` for `goal_failure_counts\[` or `goal_failure_counts.` assignments (not just reads). Confirm which phase/file writes to it and under what condition (e.g. is it incremented once per cycle the active goal shows no progress, or something narrower?). This determines how many consecutive bad cycles it actually takes before `_apply_failure_decay`'s `goal_failure_threshold` (default 2) triggers.

3. Gather real evidence: check whether `CAMPY_MCP_CMD` and live ARC API credentials are available in this environment (try `make smoke` if so, or check for an existing recent `artifacts/agent_execution_trace.json` — check its timestamp, and if it's from an earlier investigation this same day it's usable). If genuinely nothing is available, say so explicitly and reason from the code's own logic as a fallback, but flag this limitation clearly in your report rather than presenting code-only reasoning as trace-verified (this is the exact mistake A214 was corrected for — do not repeat it).

4. Using whatever real evidence is available, determine: across a realistic multi-cycle episode where the same goal stays active with `meaningful_progress=False` repeatedly, does `_apply_grounding_gate` and/or `_apply_failure_decay` actually demote/clamp that goal's ranking within a number of cycles a real episode would plausibly reach (`WorkflowLimits.max_cycles` — check its default) — or is there a realistic window (e.g. the first `goal_failure_threshold - 1` cycles, or a case where `_apply_grounding_gate`'s specific gate condition doesn't fire because a *different* hypothesis's confidence rose instead of the active goal's own) where a graph-confirmed-unproductive goal could still rank at the top?

5. Write up Track B's finding as a self-contained block of prose (2-4 paragraphs), citing exact file:line references and any real trace evidence used (with its source/timestamp named explicitly). Do NOT edit `backlog/A215.md` yourself if working as a fanned-out track-agent — report your finding back to the primary session instead.

## Track C: KPI instrumentation for `arc_confirm_hypothesis`/`arc_contradict_hypothesis`/`arc_update_goal_confidence` usage

### Scope

Independent, unconditional implementation work — not gated on Track A/B's findings, can be worked and merged separately. Same KPI family as `scripts/graph_compliance_report.py`'s existing metrics (A196/A198/A214) — read `agents/arc4/telemetry.py`'s `_has_positive_graph_evidence`/`graph_grounded` and `_has_graph_evidence_at_all`/`graph_informed` (A214) first as the established pattern to mirror exactly. Touches `agents/arc4/graph_queries.py`, `agents/arc4/telemetry.py`, `scripts/graph_compliance_report.py`.

### Steps

1. Read `agents/arc4/graph_queries.py`'s existing `_capability_missing_count`/`pop_capability_missing_count()` mechanism (A196) in full — find where `_capability_missing_count` is declared (an instance attribute on `ArcGraphQueryPort`), where it's incremented (inside `_call_tool`, the single choke point every graph call passes through), and how `pop_capability_missing_count()` returns-and-resets it. This is the exact pattern to mirror.

2. Add two new counters to `ArcGraphQueryPort`, same shape as `_capability_missing_count`:
   - `_hypothesis_confirm_contradict_count: int` — increment inside `_call_tool` (or at the two specific call sites in `record_vet`, whichever matches the existing capability-missing counter's own increment location more closely — check this, don't guess) whenever the tool name being called is `confirm_hypothesis` or `contradict_hypothesis`.
   - `_goal_confidence_write_count: int` — increment whenever the tool name being called is `update_goal_confidence`.
   - Add `pop_hypothesis_confirm_contradict_count()` and `pop_goal_confidence_write_count()`, each returning the current count and resetting to 0, exactly mirroring `pop_capability_missing_count()`'s signature and behavior.

3. In `agents/arc4/telemetry.py::_step_snapshot`, find where `capability_missing_count` is currently popped (search for `pop_capability_missing_count`) and add the same pattern for the two new counters, adding two new keys to the step snapshot dict: `hypothesis_confirm_contradict_attempted_count` and `goal_confidence_write_attempted_count` (integer counts, not booleans — mirror `capability_missing_count`'s own type exactly, do not deviate to booleans without checking why capability_missing_count is an int and matching that reasoning).

4. In `scripts/graph_compliance_report.py::report()`, add two new rate calculations alongside the existing ones (`llm_goal`, `llm_plan`, `grounded`, `informed`):
   ```python
   hypothesis_confirm_contradict_steps = sum(1 for s in steps if s.get("hypothesis_confirm_contradict_attempted_count", 0) > 0)
   goal_confidence_write_steps = sum(1 for s in steps if s.get("goal_confidence_write_attempted_count", 0) > 0)
   ```
   and add to the returned dict:
   ```python
   "hypothesis_confirm_contradict_rate_per_100": round(100 * hypothesis_confirm_contradict_steps / total, 2),
   "goal_confidence_write_rate_per_100": round(100 * goal_confidence_write_steps / total, 2),
   ```
   Place them near `graph_informed_decision_rate` in the returned dict (same section of the KPI family), not scattered elsewhere.

5. Update `show_history()`'s printed row format (search for the existing `f"...informed={row.get('graph_informed_decision_rate')}..."` line A214 added) to also print `hyp_confirm_contradict=` and `goal_conf_write=` in the same style.

### Tests

New test file (or added to a shared `tests/test_a215_*.py`):
1. `ArcGraphQueryPort`: calling `record_vet` with `vet.approved=True` and `vet.approved=False` both increment `_hypothesis_confirm_contradict_count`; calling `record_evaluation` with a `goal_id` present increments `_goal_confidence_write_count`; `pop_*` methods return-and-reset correctly (mirror the existing `pop_capability_missing_count` tests in `tests/test_a196_shift_a_c_trend_telemetry.py`'s `TestGraphQueryPortCapabilityMissingCounter` class exactly, same shape, two new counters).
2. `telemetry.py::_step_snapshot`: the two new fields appear in the snapshot dict with the correct popped values.
3. `report()`: unit tests with synthetic step lists confirming both new rates compute correctly, including the zero-steps-with-the-field-present case (mirror A214's `test_missing_graph_informed_key_defaults_to_not_informed` pattern for backward compatibility with older trace snapshots that won't have these two new keys at all).
4. A regression test confirming `graph_grounded_decision_rate`/`graph_informed_decision_rate`'s existing output is unaffected by these two additions.

### Commit and branch

This track gets its own branch (e.g. `feat/a215-track-c-hypothesis-confidence-kpis`), separate from whatever branch Tracks A/B's synthesis produces — it's independent, unconditional work. Follow this repo's standard branch+PR discipline (never commit to master). Stage precisely: `agents/arc4/graph_queries.py`, `agents/arc4/telemetry.py`, `scripts/graph_compliance_report.py`, the new test file. Do not touch `backlog/A215.md` or `backlog/masterBacklogTracker.md` from this track — the primary session updates those once all tracks report.

## Reporting back (Tracks A and B only)

If dispatched as a subagent, Track A and Track B do not push a branch or open a PR — this card's plan expects both tracks' findings to be synthesized by the primary session into a single Outcome section before anything is committed. Report your status as DONE/DONE_WITH_CONCERNS/NEEDS_CONTEXT/BLOCKED with your track's finding written out in full (ready to paste), all file:line citations, and anything you're not fully confident about. (Track C, by contrast, does push its own branch — see its own section above.)

## Synthesis (primary session, after both tracks report)

Combine Track A + Track B into one of the three outcomes described in `backlog/A215.md`'s "What this delivers" section. If a fix is warranted, implement it directly (small, targeted — see the card for the shape) with TDD: write a test proving the current `max()`-only merge fails to demote a hypothesis given a low graph confidence, confirm it fails, then implement the fix, confirm it passes. Update `backlog/A215.md`'s Outcome section with both tracks' findings plus the synthesis and (if applicable) what was implemented.

## Validation commands

```bash
# Track C (always):
.venv/bin/python -m pytest tests/test_a215_*.py -v
.venv/bin/python -m pytest tests/ -q
make test-a
make test-all
# Tracks A/B synthesis, if a fix lands:
.venv/bin/python -m pytest tests/test_a215_*.py -v
.venv/bin/python -m pytest tests/ -q
make test-a
make test-all
# Tracks A/B synthesis, if no fix lands:
make test-a
make test-all
```

## Assumptions/defaults

- Same discipline as A209/A212/A214: if the evidence doesn't clearly support a fix, document why and leave the code alone. "No change needed, here's the specific reasoning from both tracks" is a complete, valid outcome.
- Do not propose reviving `record_vet`/`confirm_hypothesis`/`contradict_hypothesis` for the action/rule case — A199 already decided against that, for reasons unrelated to this card's scope (goal-level `VictoryCondition.confidence`, not action-level `Hypothesis.status`).
- If Track A finds the `goal_id` wiring is actually broken (write and read never connect for the same goal), that is itself the headline finding and takes priority over the `max()` asymmetry question — a broken pipe upstream makes the merge-direction question moot until the pipe is fixed.
- Track C's `hypothesis_confirm_contradict_rate_per_100` reading 0.0 in every real run is the *expected, correct* result today (per A199) — do not treat a 0.0 reading as a bug in the instrumentation itself; the KPI's job is to make that zero visible and trended, not to force it nonzero.
