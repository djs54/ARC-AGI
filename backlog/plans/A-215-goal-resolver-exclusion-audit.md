# Plan: A215 — Audit: Does `goal_resolver.py` Need a Graph-Driven Exclusion Path?

## Card metadata

- ID: A215
- Priority: P2
- Layer: ARC runtime
- Dependencies: None (references A199, A208)

## Summary

`plan_generator.py` has real graph-driven exclusion (A208); `goal_resolver.py` only ever boosts confidence from graph evidence, never demotes/excludes. The one live goal-level graph signal (`VictoryCondition.confidence`, kept current every cycle via `record_evaluation` → `arc_update_goal_confidence`) is read back by `goal_resolver.py` but merged via `confidence=max(hypothesis.confidence, boost)` (`goal_resolver.py:304`) — structurally one-directional, so a low graph confidence can never pull a hypothesis's score down. Audit whether this asymmetry is a real, uncovered gap or whether local state (`_apply_grounding_gate`/`_apply_failure_decay`) already handles it.

**This card is written to be executable as two parallel, independently-dispatchable investigation tracks.** If executing via subagent fan-out, dispatch Track A and Track B as separate agents on separate branches (mirroring how A213/A214 were fanned out), then have the primary session synthesize both findings into the card's Outcome section itself — do not have either track-agent write the Synthesis section, since it needs both tracks' results.

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

## Reporting back (both tracks)

If dispatched as a subagent, do not push a branch or open a PR — this card's plan expects both tracks' findings to be synthesized by the primary session into a single Outcome section before anything is committed. Report your status as DONE/DONE_WITH_CONCERNS/NEEDS_CONTEXT/BLOCKED with your track's finding written out in full (ready to paste), all file:line citations, and anything you're not fully confident about.

## Synthesis (primary session, after both tracks report)

Combine Track A + Track B into one of the three outcomes described in `backlog/A215.md`'s "What this delivers" section. If a fix is warranted, implement it directly (small, targeted — see the card for the shape) with TDD: write a test proving the current `max()`-only merge fails to demote a hypothesis given a low graph confidence, confirm it fails, then implement the fix, confirm it passes. Update `backlog/A215.md`'s Outcome section with both tracks' findings plus the synthesis and (if applicable) what was implemented.

## Validation commands

```bash
# If a fix lands:
.venv/bin/python -m pytest tests/test_a215_*.py -v
.venv/bin/python -m pytest tests/ -q
make test-a
make test-all
# If no fix lands:
make test-a
make test-all
```

## Assumptions/defaults

- Same discipline as A209/A212/A214: if the evidence doesn't clearly support a fix, document why and leave the code alone. "No change needed, here's the specific reasoning from both tracks" is a complete, valid outcome.
- Do not propose reviving `record_vet`/`confirm_hypothesis`/`contradict_hypothesis` for the action/rule case — A199 already decided against that, for reasons unrelated to this card's scope (goal-level `VictoryCondition.confidence`, not action-level `Hypothesis.status`).
- If Track A finds the `goal_id` wiring is actually broken (write and read never connect for the same goal), that is itself the headline finding and takes priority over the `max()` asymmetry question — a broken pipe upstream makes the merge-direction question moot until the pipe is fixed.
