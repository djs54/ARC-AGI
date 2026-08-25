# Graph-Engineering Measures — Definitions and Build Status

Source framework (verbatim, provided 2026-08-25). Each measure is tagged **BUILT**, **PARTIAL**, or **NOT BUILT** against the actual state of this repo as of that date — re-verify the tag against the real code before trusting it; this file is a snapshot, not a live status page.

## Shift A: Deterministic Pre-Processing

> Statistical and deterministic logic handles raw data before the LLM wakes up.

- **Component-Level Unit Tests** — signal detection, parsing, threshold monitoring must pass traditional hard-coded unit tests, not LLM-as-judge. **BUILT** (standard practice across `agents/arc4/*.py`; no LLM-as-judge pattern exists in this codebase).
- **Phase-Isolated Token Tracking** — `PERCEIVE`, `VET`, `EVALUATE` must register exactly zero token cost. **BUILT**: `agents/arc4/telemetry.py::ArcV2Telemetry.wrap_phase` captures per-phase token deltas (A197); `agents/arc4/compliance_checks.py::check_shift_a_invariants` asserts zero cost across `DETERMINISTIC_PHASES`; `scripts/check_compliance_violations.py` is the pass/fail gate (`make check-compliance`).

## Shift B: Consolidated Reasoning

> A single agent owns reasoning; sub-agents are strictly for bounded, ephemeral data-gathering.

- **Tree-Based Trajectory Tracing (OpenTelemetry)** — every run emits a trace tree, primary agent's decision chain as root span, sub-agent probes as child spans. **NOT BUILT.** This repo emits flat JSON logs (`agent_execution_trace.json`, `master_timeline.json`) via `agents/arc4/telemetry.py`, not an OpenTelemetry span tree. There is no root-span/child-span distinction today.
- **Sub-Agent Payload Audits** — child-span payloads must be raw results, not narrative conclusions. **PARTIAL.** Candidate/goal metadata is inspectable manually (`graph_evidence`, `entity_neighborhood_grounded`, etc. are all raw structured data, not prose) but there is no automated audit that flags narrative content if it ever appeared. The A200-A206 trajectory Reasoner is the actual single-owner-of-reasoning implementation (see `backlog/A207.md` for the `second_veto` gap this caught: an episode-ending decision that used to bypass it).

## Shift C: Knowledge Graph as Control Plane

> The graph dictates the investigation space and bounds permissible paths, not passive RAG.

- **Safety & Alignment Metrics** (`invalid_action_rate`, `dissonance_trigger_rate`) — proposed actions must map to an active, un-falsified `(:Hypothesis)` edge. **NOT BUILT** as named metrics. Adjacent-but-different: `scripts/graph_compliance_report.py`'s `graph_grounded_decision_rate` (fixed 2026-08-25 to require actual positive evidence, not just any graph response — see A207) and `compliance_violation_total` (A191's exclusion invariant: no executed candidate should ever be `repeated_falsified`). Neither is literally "does this action map to an active hypothesis edge."
- **Exploration Efficiency Tracking** (`avg_steps_per_solve`) — an agent bound by the graph should systematically exhaust hypotheses and escalate/switch archetype well before any step-count ceiling. **NOT BUILT** as a tracked/aggregated metric — `steps` exists per single run but there's no rolling average across runs, no comparison logic. A207's whole-episode futility termination (`reasoner_unproductive_anchor_streak`) is a real step toward the *behavior* this measure wants (ending before the wall-clock/step ceiling when nothing is working) but it uses in-process state, not a graph query, and nothing aggregates it across runs yet.

## Graph-Guided Investigation Loop

> Anchor on an entity, test causal hypotheses via edges, evaluate evidence, terminate on root cause.

- **Outcome Chain & Valence Alignment** (`register_plan`/`report_outcome`, -1.0/+1.0 valence, abandon on negative) — **NOT BUILT** as a formal protocol. Conceptually adjacent: falsification counts (`action_falsification_counts`), `EXHAUSTED`/`RETRY` transitions in `agents/arc4/investigation_reasoner.py`, and the anchor-abandonment behavior A207 added — but there is no literal `register_plan`/`report_outcome` call pair with an explicit valence score field anywhere in this codebase. Don't describe the existing falsification-counting mechanism as satisfying this measure; it's a different, less formal thing that happens to rhyme with it.
- **Amygdala Reflex Auditing** (preemptive flag when a plan matches a historical negative-valence path's semantic footprint) — **NOT BUILT.** A186's mechanic-fusion (cross-game aggregate memory) is the closest adjacent piece — it transfers structural evidence across episodes — but it does not implement a preemptive "you're about to repeat a known failure" warning.
- **Cross-Run Regression Detection** (`solve_rate` drop >10% vs. rolling 3-run average on similar archetypes) — **NOT BUILT.** `reports/compliance_history.jsonl` (A198) trends Shift-A/C compliance rates over time, not solve rate, and has no regression-alerting logic.

## How to use this honestly

When this skill's checklist asks "is X measured," the answer is one of: (a) yes, here's the script/test that proves it, (b) partially, here's what exists and what's missing, or (c) no, and here's the closest adjacent thing if any — never "effectively yes" for something in the NOT BUILT list above just because a conceptually-similar mechanism exists. If a real piece of work would close one of these gaps, that's backlog-card material (`backlog/BacklogRules.md`), not something to claim as already satisfied.
