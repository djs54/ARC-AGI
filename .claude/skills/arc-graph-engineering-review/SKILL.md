---
name: arc-graph-engineering-review
description: Reviews ARC runtime changes and investigations against this repo's graph-engineering principles (Shift A deterministic pre-processing, Shift B consolidated single-agent reasoning, Shift C knowledge graph as control plane, and the Graph-Guided Investigation Loop) using concrete, checkable measures, not directional intent. Use when making architecture-affecting changes under agents/arc4/, arc_runtime/, or run_single_puzzle.py, before considering ARC runtime work complete, when investigating an ARC agent bug or live-run anomaly, or when the user asks whether a change follows the graph-engineering principles.
---

# ARC Graph-Engineering Review

## Why this exists

The principles (Shift A/B/C, the Graph-Guided Investigation Loop) are already written into `ARCHITECTURE.md`. Having them documented has not been sufficient — real session evidence (2026-08-25) shows a change can ship, pass every test, and still be Shift-A-shaped (in-process state) where a Shift-C-shaped fix (graph-resident tracking) was the more principle-aligned choice, without anyone flagging the tradeoff. This skill exists to force the explicit check, not to restate the principles — read `ARCHITECTURE.md`'s "Graph-Engineering Principles" section first for the actual definitions.

**This applies to your own investigation process, not just the code you ship.** If you're debugging a live-run anomaly by reading raw JSON traces and log files in your head, that is *not* the Graph-Guided Investigation Loop — it's ordinary debugging. Say so plainly if that's what's happening; don't imply graph-guidance you didn't do.

## When invoked, create a TodoWrite item per section below and answer each honestly

### 1. Shift A — deterministic pre-processing
- [ ] Does this change touch `PERCEIVE`, `VET`, or `EVALUATE`? If so, does it introduce any LLM call in those phases? (Should be zero token cost — verify with `make check-compliance` / `scripts/check_compliance_violations.py`, not by inspection alone.)
- [ ] Is any new deterministic logic (parsing, thresholding, signal detection) covered by hard-coded unit tests, or does it require an LLM-as-judge to evaluate? The latter means probabilistic logic leaked into a phase that should be deterministic.

### 2. Shift B — consolidated reasoning
- [ ] Does a sub-agent/phase in this change return a raw result (bounding box, diff, count), or a narrative conclusion ("the rule is likely a color shift")? Narrative output from a sub-agent is the single-agent constraint failing.
- [ ] Is there exactly one place that owns the advance/repeat/terminate decision for this code path, or did this change add a second place that can end an episode/investigation independently? (See `backlog/A207.md` for the concrete gap this caught: `second_veto` used to bypass the Reasoner entirely.)

### 3. Shift C — knowledge graph as control plane
- [ ] Does this decision consult/write the graph, or local process state (`WorkflowState`, an in-memory counter)? If local state, is that a deliberate, *stated* tradeoff (e.g. "A201's graph schema isn't server-side yet") — or an unexamined default?
- [ ] Would a proposed action, if traced, map to an active, un-falsified graph hypothesis/edge — or could the LLM be guessing blindly with the graph as passive context rather than a bound?

### 4. Graph-Guided Investigation Loop
- [ ] For a bug/anomaly investigation: did it anchor on a specific entity/fact, test a hypothesis, and log a pass/fail verdict anywhere queryable — or was it read-logs-and-reason-in-your-head? Name which one, don't blur them.
- [ ] Does the fix change what happens when a whole line of inquiry produces uniformly negative results (abandon-on-negative-valence), or only when a single step fails?

## What's actually measurable today vs. not yet built

Don't claim a measure passed if the infrastructure to check it doesn't exist yet. See [REFERENCE.md](REFERENCE.md) for the full measure definitions (verbatim from the 2026-08-25 framework) and the current build status of each — several (OpenTelemetry trace trees, `register_plan`/`report_outcome` valence logging, `invalid_action_rate`, `dissonance_trigger_rate`, cross-run `solve_rate` regression) are not built. Say so directly rather than approximating them from what *is* available (`scripts/graph_compliance_report.py`'s existing metrics).

## After the checklist

Report findings the same way a code review would: confirmed-compliant items, real gaps found, and — critically — whether a gap is worth a backlog card now or is an acceptable, stated tradeoff. Don't silently ship a Shift-A-shaped fix for a Shift-C-shaped problem without saying so, even if it's the pragmatic choice given current infrastructure.
