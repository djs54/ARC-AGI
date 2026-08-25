# Annatar Accountability Map

**Date:** 2026-08-25  
**Purpose:** Record, for every decision-point and workflow component in the ARC-AGI runtime, whether it currently reports to Annatar and a principle-based verdict grounded in Shift A/B/C and the Graph-Guided Investigation Loop (defined in `ARCHITECTURE.md`).

**Scope:** Passes 1–3 of A210, which renamed the "Reasoner" component to "Annatar" throughout the codebase and runtime. This map documents the state *after* that rename and audits each component against the graph-engineering principles.

---

## 1. The Six Core Phases

### 1.1 Perceive (`agents/arc4/perceive.py`)

**Current Status:**  
Perceive reaches Annatar directly. The `perception_payload` (structured GridSnapshot with entities, state hash, and observations) is a positional argument to `AnnatarPhase.__call__` / `run_annatar_cycle` in `agents/arc4/annatar_signals.py`.

**Files:** `agents/arc4/perceive.py`, `agents/arc4/annatar_signals.py`

**Principle Verdict:**  
✓ **Aligned with Shift A and Shift B**

Shift A states that deterministic pre-processing (signal detection, anomaly identification) is completed *before* the agent reasons. Perceive is purely deterministic — it parses the raw observation into a structured GridSnapshot with no LLM involved. Its result is then passed to Annatar as input. This is the correct shape: deterministic observation is complete, then a single reasoning owner (Annatar) consumes it.

**Follow-up:** None — correctly aligned.

---

### 1.2 Goal Resolution (`agents/arc4/goal_resolver.py`)

**Current Status:**  
Goal resolution reaches Annatar only indirectly. The `resolved_goal_payload` (the chosen goal_id or None) is stored on `state.active_goal`. Annatar reads `state.active_goal.selected.goal_id` only as a fallback anchor when no `entity_ref` is available in the investigation thread state. The phase itself does not pass its result directly to Annatar.

**Files:** `agents/arc4/goal_resolver.py`, `agents/arc4/workflow.py` (lines ~250–260)

**Principle Verdict:**  
✓ **Aligned with Shift B (bounded sub-agent, raw results)**

Goal resolution generates candidate goals via a tiered escalation (heuristic → graph → LLM), but its output is raw candidate metadata, not a conclusion. The phase returns a structured `ResolvedGoal` object with candidates ranked. Annatar (or the normal orchestrator flow) consumes this as input, not as a separate reasoning step. The LLM's escalation (if it fires) produces a structured JSON response, not a prose narrative — it is fully absorbed into the phase's return value before the LLM's raw output ever surfaces to Annatar with independent provenance. This matches Shift B's intent exactly: "short-lived sub-agents return raw results, never independent conclusions."

**Follow-up:** None — correctly aligned.

---

### 1.3 Plan Generation (`agents/arc4/plan_generator.py`)

**Current Status:**  
Plan generation does not reach Annatar directly. The `planning_payload` (the proposed action sequence) is never stored on `state` and is not passed as a direct argument to Annatar. The phase result reaches Annatar only as a side effect: when a candidate is executed, Annatar later reads `execution.candidate.metadata["entity_ref"]` to determine what entity/action was tried. Plan generation itself is structurally isolated from Annatar's inputs.

**Files:** `agents/arc4/plan_generator.py`, `agents/arc4/executor.py`

**Principle Verdict:**  
✓ **Aligned with Shift B**

Like `goal_resolver`, plan generation returns raw structured results (candidate actions, ranked by heuristic/graph scoring, with optional LLM escalation for ambiguous cases). Its LLM calls are bounded and schema-constrained. The escalation produces a candidate ID or action, not a narrative. Annatar never reads plan generation's output directly — it only learns *which* action was executed (the side effect of executor consuming a plan candidate). This preserves the bounded-sub-agent constraint: plan generation proposes, the orchestrator disposes, Annatar reasons about what happened.

**Follow-up:** None — correctly aligned.

---

### 1.4 Plan Vetting (`agents/arc4/plan_vetter.py`)

**Current Status:**  
Plan vetting is structurally isolated on the common (single-veto) path. When the vetter rejects a candidate on the *first* veto, the rejection reason and an alternative candidate are written to `state.latest_veto_reason` and `state.latest_veto_alternative`. These are consumed by a same-cycle local replan loop that never invokes Annatar. Only on a *second* consecutive veto does the path route through Annatar (via `_route_second_veto_through_annatar` in `workflow.py`, added by card A207). This is a deliberate two-tier structure, not accidental isolation.

**Files:** `agents/arc4/plan_vetter.py`, `agents/arc4/workflow.py` (lines ~237–250)

**Principle Verdict:**  
⚠ **Identified Gap, Already Tracked**

On the first veto, plan_vetter's rejection is a deterministic signal (Shift A compliant) that *should* inform the decision-making process, but it independently triggers a replan without ever informing Annatar. This violates Shift B's end-to-end reasoning principle: a deterministic signal that ends or redirects an investigation (by forcing an immediate replan without Annatar's input) is the same structural problem as `second_veto` posed (fixed by A207).

However, this gap is acknowledged and documented in **card A212** (a separate audit, linked from `backlog/A210.md`), which will investigate whether first-veto rerouting through Annatar is correct or whether the current two-tier structure is justified by the control-flow economics.

**Follow-up:** **A212** — confirmed-existing audit card.

---

### 1.5 Execution (`agents/arc4/executor.py`)

**Current Status:**  
Execution reaches Annatar directly. The `execution_payload` (the actual action sent, the grid observation post-action, and the raw API response) is a direct positional argument to `run_annatar_cycle` in `annatar_signals.py`.

**Files:** `agents/arc4/executor.py`, `agents/arc4/annatar_signals.py`

**Principle Verdict:**  
✓ **Aligned with Shift A and Shift B**

Execution is deterministic: it sends a pre-authorized action to the ARC API and records the observation. No LLM involved. The result is then forwarded to Annatar (the single reasoning owner) for evaluation. Annatar can inspect what was tried and what changed, feeding both into its investigation-state reasoning.

**Follow-up:** None — correctly aligned.

---

### 1.6 Evaluation (`agents/arc4/evaluator.py`)

**Current Status:**  
Evaluation is positioned to reach Annatar directly as a positional argument, *except* for the environment-terminal short-circuit case (real ARC-API win/loss). When `termination_from_evaluation` fires (a genuine puzzle victory or failure per the external ARC API), the episode ends immediately without invoking Annatar at all. This is a deliberate exemption documented in the Trajectory Annatar design spec (section 5).

**Files:** `agents/arc4/evaluator.py`, `agents/arc4/workflow.py` (lines ~225–230)

**Principle Verdict:**  
✓ **Aligned with Shift A and Shift C (context-appropriate exemption)**

Evaluation is deterministic: it compares the grid state against predicted outcomes and scores progress. Its LLM call (if any) is a bounded "why did this happen?" escalation for ambiguous frames, not a decision-maker. When the evaluator observes a real win or loss from the ARC API, it returns a terminal status. The environment-terminal exemption is correct: the ARC API's own declaration (win/loss) is an *authoritative external fact*, not a system-internal judgment that Annatar should review. Annatar has no authority over the real world's ruling; it only reasons about strategy when the outcome is ambiguous or in-progress. Once the external authority speaks, the episode is over.

**Follow-up:** None — correctly aligned and exempted.

---

## 2. WorkflowOrchestrator.run() Control-Flow Exits

### 2.1 Environment-Terminal (termination_from_evaluation)

**Current Status:**  
Deliberately exempt. The environment-terminal check (real ARC-API win/loss from the evaluator) runs *before* Annatar is ever invoked, short-circuiting the entire reasoning cycle.

**Files:** `agents/arc4/workflow.py` (lines ~225–230)

**Principle Verdict:**  
✓ **Aligned with Shift A/B/C (external authority)**

See section 1.6 above. The ARC API's own win/loss declaration is an authoritative external fact that does not require strategic reasoning.

**Follow-up:** None.

---

### 2.2 Annatar's Own `decision == "terminate"`

**Current Status:**  
Routes through Annatar directly. This is Annatar's own output — the investigation-thread state machine has decided (via graph signals, deterministic transition rules, or bounded LLM escalation) to terminate the investigation thread, which triggers episode termination.

**Files:** `agents/arc4/workflow.py` (lines ~268–269)

**Principle Verdict:**  
✓ **Aligned with Shift B**

This is the intended case: the single reasoning owner (Annatar) decides the episode is over and is held accountable for that decision.

**Follow-up:** None.

---

### 2.3 Stall (stall_reason), No-Annatar-Configured Branch

**Current Status:**  
Only reachable when no Annatar is configured at all (legacy fallback, `self._dependencies.reason is None`). When Annatar *is* configured, the stall signal (from `check_stall`, `force_explore_after`, `_action_space_exhausted`) is folded into `CycleSignals` and passed to Annatar as input, not as an independent return path.

**Files:** `agents/arc4/workflow.py` (lines ~230–240)

**Principle Verdict:**  
✓ **Aligned when Annatar is configured; legacy-acceptable when not**

When Annatar is present, stall is Shift A compliant (a deterministic signal) and Shift B compliant (Annatar consumes it as input). When Annatar is `None`, the code falls back to today's pre-Annatar behavior, which is acceptable for backward compatibility but does not satisfy Shift B.

**Follow-up:** None — the no-Annatar branch is explicitly temporary.

---

### 2.4 Exception → CRASHED

**Current Status:**  
Does **NOT** route through Annatar or the graph at all. An unhandled exception in the main loop (whether during a phase, Annatar itself, or anywhere in the orchestrator) causes the episode to end with `WorkflowStatus.CRASHED` and no investigation-thread closure recorded in the graph.

**Files:** `agents/arc4/workflow.py` (lines ~280–290)

**Principle Verdict:**  
🔴 **Real Gap — Confirmed**

This violates Shift B's end-to-end reasoning principle: an investigation thread is left dangling in the graph forever (in whatever state it was in when the crash occurred), with no record of why or when it ended. The graph-control-plane premise (Shift C) fails: the graph is not consulted or updated for the most severe failure mode.

**Follow-up:** **A211** — confirmed-existing gap card for crash-safety and thread closure.

---

### 2.5 Budget Exhausted (_route_budget_through_annatar)

**Current Status:**  
Routes through Annatar for visibility and bookkeeping. When `check_budget` fires on a later iteration (not the first cycle), the orchestrator constructs fresh synthetic perception/execution/evaluation payloads and invokes `run_annatar_cycle` with `stall_reason="budget_exhausted"`. Annatar's returned decision is deliberately *never* inspected — the method returns `_finish(..., BUDGET_EXHAUSTED, ...)` unconditionally, regardless of what Annatar decided.

**Files:** `agents/arc4/workflow.py` (lines ~312–327)

**Principle Verdict:**  
✓ **Aligned with Shift B (visibility) with Hard Ceiling Preserved (Shift A)**

A209's audit resolved this: the budget ceiling is a hard, non-negotiable system constraint (Shift A), but its assertion must still pass through the reasoning owner (Shift B) for visibility and bookkeeping. Annatar can record why the thread ended (budget exhaustion) and close out any open state cleanly in the graph, even though Annatar has no authority to override the ceiling. The hard ceiling is preserved (Annatar cannot extend the episode past `max_cycles`), but the single reasoning owner is informed. This satisfies both principles.

**Follow-up:** None — A209's finding is already implemented and correct.

---

### 2.6 Second Veto (_route_second_veto_through_annatar)

**Current Status:**  
Routes through Annatar. When the plan vetter rejects a candidate *twice* in a row (two consecutive vetoes on different candidates for the same goal), the second rejection is not an independent termination — instead, Annatar is invoked with `stall_reason="repeated_veto"` and a synthetic execution result. Annatar's decision (continue, deepen, or terminate) genuinely drives control flow: a `"terminate"` decision ends the episode; other decisions allow looping.

**Files:** `agents/arc4/workflow.py` (lines ~240–250)

**Principle Verdict:**  
✓ **Aligned with Shift B**

A207 correctly identified that `second_veto` was an independent termination path that bypassed Annatar, violating end-to-end reasoning. The fix routes it through Annatar. The first veto still triggers local replan (Shift A: deterministic signal handled locally), but the second veto escalates to the reasoning owner.

**Follow-up:** None — A207 correctly closed this gap.

---

## 3. Temporal Workflows (`agents/arc4/temporal_workflows.py`)

**Current Status:**  
Deprecated (2026-08-23) and structurally isolated. No Annatar wiring exists in this file. The `ArcPuzzleWorkflow` (Temporal.io-backed execution path, opt-in via flag, never the default) is a mechanical port of the exact same phase sequence that the plain orchestrator runs, with zero reasoning added.

**Files:** `agents/arc4/temporal_workflows.py`

**Principle Verdict:**  
✓ **Exempt by Design (Deprecated/Unused)**

Temporal.io was intended to provide durable orchestration but never became the decision-making layer. The Annatar design (graph-backed state machine) is the now-preferred durability substrate. Temporal workflows are left in place but not extended. No Shift A/B/C compliance is required for deprecated code.

**Follow-up:** None — exempt by design.

---

## 4. World-Model Evaluation Path (`benchmarks/arc3/world_model_eval.py`)

**Current Status:**  
Purely observational telemetry. The `world_model_eval` path (gated by a flag) records metrics about what already happened (node/edge counts, reasoning-mode counters, etc.) to a separate JSONL telemetry artifact. It does not feed back into `WorkflowOrchestrator`'s dependencies or Annatar's inputs anywhere. This is read-only, post-hoc analysis.

**Files:** `benchmarks/arc3/world_model_eval.py`

**Principle Verdict:**  
✓ **Exempt by Design (Read-Only Telemetry)**

This component is not a decision-maker and does not compete with Annatar. It cannot structurally report to Annatar (Annatar is reasoning *during* the episode; this analysis happens *after*). Shift A/B/C do not apply to offline telemetry.

**Follow-up:** None — exempt by design.

---

## 5. Agents Common Utilities (`agents/common/`)

**Current Status:**  
`failure_taxonomy.py`, `grid_hash.py`, `trace_names.py` are pure utility modules: classification helpers, hashing, and naming conventions. They contain no autonomous decision logic and hold no state.

**Files:** `agents/common/failure_taxonomy.py`, `agents/common/grid_hash.py`, `agents/common/trace_names.py`

**Principle Verdict:**  
✓ **Exempt by Design (Utilities, Not Decision-Makers)**

Utility functions do not need to report to Annatar; they are tools, not agents. Shift A/B/C apply to decision-making components.

**Follow-up:** None — exempt by design.

---

## 6. Benchmarks and Offline Scoring (`benchmarks/arc3/`)

**Current Status:**  
`trajectory_eval.py`, `outcome_judge.py`, `regression_monitor.py`, and `benchmarks/ab_harness.py` are offline, post-hoc scoring and analysis tools. They run *after* a trajectory has already completed, operating on trace artifacts. They cannot structurally report to Annatar since the episode Annatar was reasoning over is already finished.

**Files:** `benchmarks/arc3/trajectory_eval.py`, `benchmarks/arc3/outcome_judge.py`, `benchmarks/arc3/regression_monitor.py`, `benchmarks/ab_harness.py`

**Principle Verdict:**  
✓ **Exempt by Design (Offline/Batch Analysis)**

These are scoring/evaluation tools, not runtime decision-makers. Per `CLAUDE.md`, `benchmarks/arc3/` is exempt from the MCP-seam rule (A030) because it embeds the brain directly for offline submission packaging. It cannot be part of a live reasoning loop. Shift A/B/C do not apply to batch analysis.

**Follow-up:** None — exempt by design.

---

## 7. LLM Escalation Tiers (`goal_resolver.py::_query_llm`, `plan_generator.py::_query_llm`)

**Current Status:**  
Annatar's inputs (from `ports.py::AnnatarPhase.__call__` signature and `annatar_signals.py::run_annatar_cycle`) are exactly:  
```python
(state, perception, execution, evaluation, *, graph_port, stall_reason)
```

There is no LLM-call-specific field anywhere. Each `_query_llm` call's raw output is fully folded into its owning phase's own return value (e.g., `ResolvedGoal` or `PlanningResult`) before that phase even returns. The LLM's raw output never surfaces to Annatar with independent provenance; Annatar only sees the phase's structured, absorbed result.

**Files:** `agents/arc4/goal_resolver.py` (lines ~180–210), `agents/arc4/plan_generator.py` (lines ~150–180)

**Principle Verdict:**  
✓ **Correctly Aligned with Shift B (Confirmed-Correct Pattern)**

This is the exact shape Shift B intends: short-lived LLM sub-agents generate hypotheses and proposals, but they return raw, structured results, never narrative conclusions. The LLM's voice is absorbed into the phase's own conclusion before it ever surfaces to Annatar. Annatar then reasons about "this phase produced X result," not "the LLM said Y" — Annatar never hears the LLM's independent voice, only the phase's structured output. This is a **confirmed-correct** pattern, not a gap. Do not flag it as suspicious just because the LLM's raw output doesn't separately surface.

**Follow-up:** None — correctly aligned.

---

## Summary

| Component | Reporting | Verdict | Follow-up |
|---|---|---|---|
| 1.1 Perceive | Direct | ✓ Aligned | None |
| 1.2 Goal Resolver | Indirect (via state) | ✓ Aligned | None |
| 1.3 Plan Generator | Indirect (side effect) | ✓ Aligned | None |
| 1.4 Plan Vetter | Partial (2nd veto only) | ⚠ Gap tracked | A212 |
| 1.5 Executor | Direct | ✓ Aligned | None |
| 1.6 Evaluator | Direct (except env-term) | ✓ Aligned + Exempt | None |
| 2.1 Env-Terminal | Exempt | ✓ Aligned | None |
| 2.2 Annatar Terminate | Direct | ✓ Aligned | None |
| 2.3 Stall (no-Annatar) | Legacy fallback | ✓ Acceptable | None |
| 2.4 Exception/Crashed | None | 🔴 Gap confirmed | A211 |
| 2.5 Budget Exhausted | Direct (visibility) | ✓ Aligned | None |
| 2.6 Second Veto | Direct | ✓ Aligned | None |
| 3 Temporal Workflows | Not wired | ✓ Exempt | None |
| 4 World-Model Eval | Not applicable | ✓ Exempt | None |
| 5 Common Utilities | Not applicable | ✓ Exempt | None |
| 6 Offline Scoring | Not applicable | ✓ Exempt | None |
| 7 LLM Escalation Tiers | Absorbed in phase result | ✓ Correct | None |

**Real gaps identified:** A211 (crash-safety/thread closure), A212 (first-veto routing audit).  
**Correctly aligned:** All other components.  
**Exempt by design:** Deprecated code, utilities, offline analysis.

---

## Reference

- `ARCHITECTURE.md`: Graph-Engineering Principles (Shift A/B/C), Decision Ownership, Implementation Track
- `docs/superpowers/specs/2026-08-23-trajectory-reasoner-design.md`: Annatar design spec
- `backlog/A207.md`: Second-veto routing (closed gap)
- `backlog/A209.md`: Budget routing audit (correctly resolved)
- `backlog/A210.md`: Annatar rename (this pass's parent card)
- `backlog/A211.md`: Crash-safety audit (open gap)
- `backlog/A212.md`: First-veto routing audit (open gap)
