# Trajectory Reasoner — Design Spec

**Date:** 2026-08-23
**Status:** approved by user, pending self-review + user sign-off on this document, then decomposition into A-series backlog cards
**Depends on (already landed):** A190-A199, ARCHITECTURE.md's "Graph-Engineering Principles (Shift A/B/C)" section

## 1. Context and motivation

ARCHITECTURE.md's Shift B entry (added earlier today, 2026-08-23) states plainly that no agent — human, LLM, or orchestrator — currently owns end-to-end trajectory reasoning in this runtime. `WorkflowOrchestrator` explicitly does not reason ("routes phases, enforces gates"). Decisions about whether to continue, retry, or abandon a line of investigation are a scatter of independent, phase-scoped gates (`check_budget`, `check_stall`, `force_explore_after`, `_action_space_exhausted`) that each read a narrow slice of `WorkflowState` and fire independently, with no single point of reconciliation.

`agents/arc4/temporal_workflows.py` (`ArcPuzzleWorkflow`) was originally intended to be this owning layer. On inspection (this session) it is a mechanical port of the exact same fixed phase sequence and the exact same gates the plain orchestrator already runs — Temporal's durability/retry machinery, with zero reasoning added. It is being marked deprecated in ARCHITECTURE.md as part of this session's work; this spec does not build on it.

This spec defines a **Reasoner**: a component that runs once per cycle, after `evaluate`, reads the accumulated trajectory state (not just the current cycle's output), and makes an authoritative decision — advance to a new goal/entity, deepen the current one, retry the exact last action, or terminate — via an explicit state machine. Its decisions are durable and queryable because they are written to the graph as first-class facts, not held in a Python variable or a Temporal event log.

## 2. Goals

- One component owns the advance/repeat/terminate decision every cycle, replacing the scattered-gate pattern (the gates become inputs, not independent deciders), except for the hard step-count circuit breaker (`check_budget`), which stays independent by design (a safety net against the Reasoner's own logic having a bug).
- The decision is deterministic-first: an explicit state machine reads graph signals and `WorkflowState`, escalating to a bounded, schema-constrained LLM call only when the state machine genuinely cannot decide (mirrors the existing `goal_resolver`/`plan_generator` escalation pattern — LLM proposes, graph-permissible-transition-set bounds what it's allowed to pick).
- The decision and its supporting state are durable in the graph (Kuzu, via hippocampy), not in Temporal or in-process memory only — a fresh process can resume an in-progress attempt by querying the graph, not by replaying an event log.
- Resuming after a crash never risks double-acting on the real, live ARC API: the one non-idempotent boundary (an action actually sent to the external API) is reconciled against the API's own real observation on resume, never assumed from local bookkeeping alone.
- Graceful degradation: if the graph is unreachable for a given cycle, the Reasoner falls back to today's existing gate logic for that cycle only (same non-strict-MCP policy used everywhere else in this codebase) and the cycle is flagged in telemetry as degraded/non-durable.

## 3. Non-goals

- Not replacing or restructuring the six existing phases (`perceive`/`resolve`/`plan`/`vet`/`execute`/`evaluate`) — the Reasoner is a new step after `evaluate`, not a rewrite of the pipeline.
- Not building on or reviving `agents/arc4/temporal_workflows.py` / Temporal.io. Durability is graph-based per this spec.
- Not building a general-purpose distributed execution engine. The crash-recovery scope is narrow and specific: "which cycle was in flight for which attempt, and did its action actually reach the real API."
- Not changing `goal_resolver.py`'s or `plan_generator.py`'s own internal scoring logic, beyond the one required integration point (anchor-biasing — see §5).

## 4. State machine

Scoped per **investigation thread** — one active thread per (attempt, anchor), where an anchor is a goal_id or entity_ref the Reasoner is currently focused on. A new thread starts when the Reasoner decides to advance to a new anchor.

### 4.1 States

| State | Meaning |
|---|---|
| `EXPLORING` | No real evidence yet for this anchor; still trying baseline untested actions. |
| `DEEPENING` | Some support exists for a hypothesis on this anchor; more evidence wanted before concluding. |
| `AWAITING_LLM` | Evidence is genuinely ambiguous/contradictory; the deterministic transition table can't decide. |
| `SATISFIED` | Confirming evidence crossed a real threshold (rule/hypothesis confidence, or actual `meaningful_progress`). Terminal for this thread — triggers advance to a new anchor. |
| `EXHAUSTED` | Every candidate hypothesis for this anchor is falsified, or the graph confirms no untested actions remain for it. Terminal for this thread — triggers advance to a new anchor. |
| `RETRY` | The *execution/observation* was inconclusive or flaky (not the hypothesis) — re-run the identical action once before treating the result as real evidence. |

### 4.2 Transition table

| From | Signal | To |
|---|---|---|
| `EXPLORING` | Partial support (some confidence, not yet threshold) | `DEEPENING` |
| `EXPLORING` | Strong immediate confirmation (`meaningful_progress` or high-confidence rule/hypothesis) | `SATISFIED` |
| `EXPLORING` | Immediately falsified, no untested alternatives for this anchor | `EXHAUSTED` |
| `EXPLORING` | Execution/observation inconclusive (e.g. no clear grid diff, ambiguous API response) | `RETRY` |
| `DEEPENING` | Confidence crosses threshold | `SATISFIED` |
| `DEEPENING` | All remaining hypotheses on this anchor falsified | `EXHAUSTED` |
| `DEEPENING` | Still ambiguous after `N` deepening cycles (configurable limit) | `AWAITING_LLM` |
| `DEEPENING` | More evidence needed, not yet ambiguous enough to escalate | `DEEPENING` (stay) |
| `AWAITING_LLM` | LLM's vote — same schema-constrained-JSON pattern `goal_resolver`/`plan_generator` already use (a fixed required-fields response, not free text); the parsed answer is then validated against the specific set of transitions the graph currently permits for this anchor (e.g. `EXHAUSTED` is not a legal answer if the graph still reports untested actions for it) before being accepted — an out-of-set answer is treated as a parse failure, not honored | `DEEPENING` \| `SATISFIED` \| `EXHAUSTED` |
| `AWAITING_LLM` | LLM call fails/times out after bounded retries | fall back to whichever of `SATISFIED`/`EXHAUSTED`/`DEEPENING` the existing deterministic signals already favor (never stuck) |
| `RETRY` | Retried action produces a clear result | re-evaluate from `EXPLORING`/`DEEPENING` transition rules using the fresh result |
| `RETRY` | Retried action is *also* inconclusive | `EXHAUSTED` (do not retry indefinitely — one retry only, then give up on this anchor) |

`SATISFIED` and `EXHAUSTED` both mean "this thread is done" — the difference is purely for telemetry/audit (did we succeed or give up), not for control flow. Both result in the orchestrator picking a new anchor next cycle via the normal `resolve` phase, unmodified.

## 5. Integration with `WorkflowOrchestrator`

Exact hook point, `agents/arc4/workflow.py::WorkflowOrchestrator.run`, line 144 (current code, subject to drift — confirm before implementing):

```python
self._record_execution_attempt(state, execution_payload)
self._record_evaluation_state(state, execution_payload, evaluation_payload)
state.step_index += 1
# <-- Reasoner hooks in here, before the existing stall/termination checks below -->
```

The Reasoner's decision (`ADVANCE | REPEAT_DEEPEN | REPEAT_RETRY | TERMINATE`) is consumed as follows:
- `TERMINATE` → same `_finish(...)` return path as today's `stall_reason`/`termination` checks, with its own reason string (distinguishing `reasoner_satisfied` from `reasoner_exhausted` from today's existing termination reasons — extend `classify_v2_termination` accordingly, don't overload existing reason strings).
- `ADVANCE` → loop continues normally; `resolve`/`plan` run unconstrained next cycle (today's existing behavior).
- `REPEAT_DEEPEN` / `REPEAT_RETRY` → loop continues, but `resolve` and `plan` must be biased toward the current anchor. This requires a new, small integration point in both `goal_resolver.py` and `plan_generator.py`: an optional `anchor_hint` parameter (goal_id or entity_ref + required action_id/book_id for `REPEAT_RETRY` specifically) that, when present, constrains/strongly prefers that anchor's candidates over the unconstrained scoring path. This must be additive — when `anchor_hint` is `None` (today's every call site), behavior is unchanged.

**Important correction from self-review — the existing `check_stall`/`termination_from_evaluation` calls (lines ~161-172 today) must not remain independent, parallel decision points once the Reasoner exists.** §2's own stated goal is that the scattered gates become *inputs* to one decision, not that they keep independently deciding to terminate alongside a second, new decider — leaving them as-is would silently reintroduce the exact "two things can independently decide to stop, possibly inconsistently" problem this whole design exists to close. Concretely:
- `check_stall`'s signal becomes one of the inputs the Reasoner's transition table reads (feeds toward `EXHAUSTED`), not a second, independent return path out of `run()`.
- `termination_from_evaluation` (mapping the evaluator's own `decision`/`reason` to a hard terminal status, e.g. a real win/loss from the ARC API) is the one exception worth keeping structurally separate *in addition to* the Reasoner — it reflects the environment's own authoritative terminal signal (the puzzle was actually solved or actually failed per the ARC API itself), not a strategic judgment call the Reasoner should be second-guessing. Keep this check exactly as-is, still independent, but position it so it can short-circuit *before* the Reasoner even runs (an environment-terminal result doesn't need a strategic opinion).
- `check_budget` remains fully independent, unchanged, at the top of the loop (line 44) — already structurally separate from everything discussed here.

Net effect on `run()`'s control flow: `check_budget` (top of loop, unchanged) → phases run → environment-terminal check (`termination_from_evaluation`, unchanged, short-circuits before the Reasoner) → **Reasoner runs, consuming `check_stall`'s signal and the other gate signals as inputs, not as separate return paths** → Reasoner's decision drives what happens next.

**Dependency injection:** `agents/arc4/ports.py::WorkflowDependencies` gains one new optional field:

```python
@dataclass(slots=True)
class WorkflowDependencies:
    perceive: PerceivePhase
    resolve: ResolvePhase
    plan: PlanPhase
    vet: VetPhase
    execute: ExecutePhase
    evaluate: EvaluatePhase
    reason: ReasonerPhase | None = None  # new, optional — None means "no Reasoner, run exactly as today"
```

A new `ReasonerPhase` Protocol, alongside the existing `PerceivePhase`/`ResolvePhase`/etc. in `ports.py`.

## 6. Graph schema (hippocampy-side ask)

```
(:Attempt {task_id, game_id, started_at})
(:InvestigationThread {thread_id, task_id, anchor_ref, anchor_type, state, state_updated_at})
-- anchor_type is a literal enum: "goal" | "entity" -- which of the two ANCHORED_ON
-- targets below applies, so a reader doesn't have to infer it from the edge alone.
(:Attempt)-[:HAS_THREAD]->(:InvestigationThread)
(:InvestigationThread)-[:ANCHORED_ON]->(:GridEntity | :Hypothesis)   -- whichever anchor_type applies
(:InvestigationThread)-[:HAS_CYCLE]->(:Cycle {step, decision, action_sent, action_confirmed_by_observation, started_at, completed_at})
(:Cycle)-[:NEXT]->(:Cycle)
```

Design constraints established during this session's graph-solutions review, non-negotiable:
- `InvestigationThread.state` is a **direct, indexed property** (primary-key-style lookup by `(task_id, anchor_ref)`), not something requiring a traversal to determine. Resume must be an O(1) lookup.
- `Cycle` nodes hang off `InvestigationThread` (per-attempt, bounded), **never** attached directly to the persistent `GridEntity`/`Hypothesis`/`Rule` nodes that the aggregate cross-game memory layer (A179/A186) depends on for fast queries — this would turn frequently-revisited entities into supernodes over the system's lifetime.
- `action_sent`/`action_confirmed_by_observation` are written in that order, synchronously, `action_sent=true` written *before* the real API call is made (write intent first, always — see §7).

**New MCP tools needed (hippocampy-side, mirrors the A192/B359 handoff pattern used earlier this session):**
- `arc_start_or_resume_thread(task_id, anchor_ref, anchor_type) -> {thread_id, state, resumed: bool, last_cycle: {...} | null}` — read/create.
- `arc_write_thread_state(thread_id, state) -> {ok}` — the durable decision write.
- `arc_write_cycle(thread_id, step, action_sent) -> {cycle_id}` — write-ahead call, before the real API action is sent.
- `arc_confirm_cycle(cycle_id, decision, confirmed: bool) -> {ok}` — after the API call returns.

This is a real cross-repo dependency; write the handoff doc (`docs/handoff/B<next>-trajectory-reasoner-schema.md`, following the exact template `docs/handoff/B278-entity-neighborhood-query.md` used) once this spec is approved and before implementation starts on the ARC-side consumer, same sequencing as A192/B359.

## 7. Resume / crash-safety design

**On process startup**, before starting a new attempt: call `arc_start_or_resume_thread` for the current task_id. If `resumed: true` and `last_cycle.action_sent and not last_cycle.action_confirmed_by_observation`:
1. Query the real ARC API for the current observation (not our own bookkeeping).
2. Compare against the predicted effect of the in-flight action (if recoverable) or simply treat the current real observation as ground truth for "what step are we actually on."
3. Call `arc_confirm_cycle` with the reconciled outcome (`confirmed: true` if the action's effect is visible in the real grid state, `confirmed: false` — meaning it never landed — otherwise).
4. Continue the loop from the reconciled state, using the *real* observation, not a replayed/assumed one.

If `resumed: false` (or the graph itself is unreachable — see §8), proceed as today: bootstrap a fresh attempt.

**Write-ahead ordering is the load-bearing invariant of this whole section:** `action_sent=true` must be written to the graph *before* the real API call is made, unconditionally, even though this means occasionally writing `action_sent=true` for an action that, due to a crash between the write and the call, never actually got sent. That's fine and intentional — the reconciliation step (step 1-3 above) always re-derives ground truth from the real observation on resume; the flag only narrows *when* that check is needed, it is never trusted on its own.

## 8. Error handling

- **Graph unreachable for a given cycle:** Reasoner falls back to today's pre-existing gate logic (`check_stall`, `force_explore_after`, `_action_space_exhausted`) for that cycle only. The cycle is flagged in telemetry (new field, extends A196: `reasoner_degraded: bool`) so a run's compliance report shows how much of it was graph-owned vs. fallback, rather than silently treating a degraded cycle as normal.
- **LLM call fails/times out inside `AWAITING_LLM`:** bounded retries (reuse the existing retry/timeout conventions from `goal_resolver`/`plan_generator`'s own escalation calls), then fall back per the transition table's own fallback rule (§4.2) — never left permanently stuck in `AWAITING_LLM`.
- **`check_budget`'s hard step ceiling** remains completely independent of the Reasoner and the state machine — it must fire even if the Reasoner has a bug that would otherwise loop forever.

## 9. Testing strategy

- **State-transition conformance suite:** one test per row in §4.2's table (not scenario-only coverage) — pure function tests against the state-machine module, no graph/LLM/API involved.
- **Resume/crash-reconciliation tests:** simulate a crash at the `action_sent=true, action_confirmed=false` window; mock the real-API observation both ways (action landed / did not land); assert correct reconciliation both times. Highest-priority test in this spec — the one place a bug means double-acting on the real, live ARC API.
- **Degraded-mode fallback test:** graph unreachable mid-cycle; assert clean fallback to existing gate logic and correct `reasoner_degraded` telemetry flag.
- **Anchor-biasing integration tests:** `goal_resolver`/`plan_generator` with `anchor_hint` set vs. unset (`None`), confirming `None` produces byte-for-byte identical output to today (regression guard) and a set hint actually constrains/biases the result.
- **Fixture-graph tests against real Kuzu** (not just mocked ports) for the two hottest queries — `arc_start_or_resume_thread`'s resume lookup, and whatever query determines "is this action within the graph's permissible transition set" — per the graph-solutions skill's own recommendation, mirroring how A192/A199 were ultimately live-verified against the real daemon rather than trusted on unit tests alone.

## 10. Implementation decomposition (for A-series backlog cards)

Each of these is intended to become one `backlog/Axxx.md` + `backlog/plans/A-xxx-*.md` pair, sized and scoped the way A190-A199 were — concrete enough for a haiku subagent to execute against a fully-specified plan, reviewed before acceptance. Suggested order (dependency-aware, mirrors this session's A193-style sequencing doc):

1. **State-machine module** (`agents/arc4/investigation_reasoner.py`, pure functions, stdlib only, mirrors `cycle_policy.py`'s style exactly) — implements §4's table as testable functions, no graph/LLM/API dependency. Fully unit-testable in isolation; no cross-repo dependency; can start immediately.
2. **Hippocampy handoff doc + `ports.py`/`graph_queries.py` client stubs** — write the handoff doc per §6, add `ReasonerPhase` Protocol to `ports.py`, add the four new `graph_queries.py` methods (`start_or_resume_thread`, `write_thread_state`, `write_cycle`, `confirm_cycle`) following the established degrade-on-`capability_missing` pattern — client-side complete and correct even before hippocampy's schema lands, same rollout discipline as A192.
3. **`WorkflowOrchestrator` integration** (§5's hook point + `WorkflowDependencies.reason` field) — wires the state-machine module + graph client into the existing loop, additive (`reason=None` preserves today's exact behavior).
4. **Anchor-biasing in `goal_resolver.py`/`plan_generator.py`** — the `anchor_hint` parameter, additive, `None`-preserves-behavior as the explicit regression guard.
5. **Resume/crash-safety logic** (§7) — the startup check + write-ahead ordering + reconciliation against the real API. Depends on (2) for the graph tools and (3) for the orchestrator hook existing.
6. **Error handling + degraded-mode telemetry** (§8) — the `reasoner_degraded` field, LLM-failure fallback. Depends on (3).
7. **Full test suite** (§9) — can be built incrementally alongside each of the above, but the crash-reconciliation and fixture-graph tests specifically should land with (5), not deferred.

Cards 1 and 2 have no dependency on each other and can be built in parallel. Everything else is sequential per the dependencies noted.

## 11. Open questions / risks carried forward, not resolved by this spec

- The exact confidence thresholds for `DEEPENING → SATISFIED` and the `N` deepening-cycles limit before `AWAITING_LLM` are not chosen here — no empirical basis yet (same honest gap A192's `entity_neighborhood_weight` default was flagged with). Pick reasonable starting values during implementation, document them as starting points, not tuned constants.
- Whether `agents/arc4/temporal_workflows.py` should eventually be deleted (vs. left deprecated-in-place) is explicitly out of scope for this spec — a separate future decision.
- This spec assumes one active `InvestigationThread` at a time per attempt. Whether multiple anchors could ever be investigated concurrently within one attempt is not addressed — out of scope, revisit if it becomes a real need.
