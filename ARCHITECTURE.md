# ARC_AGI Architecture

> Canonical architecture reference for the `ARC_AGI` sibling repo.
> This document covers the ARC solver, harness, and the dependency boundary to HippoCampy/Campy.

## Mission

`ARC_AGI` is the benchmark and solver repo.

Its job is to:

- run ARC-AGI experiments
- host the ARC solver/orchestration logic
- evaluate strategy, prompt shape, and runtime behavior
- use HippoCampy/Campy as the external local-memory substrate

It is not the memory engine.

## ARC-AGI-3 Strategic Architecture

Technical mission statement:

> GPT-5.5-style reasoning should generate hypotheses, but the graph world model should decide what is believed, what is falsified, what transfers, and what experiment is worth paying for next.

ARC-AGI-3 work is moving from raw episode memory toward a graph-backed
world-model compiler. Local observations are not enough. Each action/effect
observation must be compiled into a per-game model of causal power, object
relevance, goal progress, falsified beliefs, and next experiments.

This makes graph memory central architecture, not a recall side channel. The
LLM can imagine hypotheses and propose experiments, but durable belief state
belongs to the world model. Future reasoning should operate against the
compiled world model rather than raw step history whenever possible.

Graph fit:

- The workload is relationship-heavy: actions cause effects, effects support
  or contradict hypotheses, hypotheses explain mechanics, mechanics transfer
  across games, and global strategy emerges from those relationships.
- The preferred model is a labeled property graph. ARC runtime needs
  traversal-first operational behavior, edge properties for confidence and
  provenance, and compact relationship-scoped facts.
- RDF/ontology is not the default for this layer. Interoperability and formal
  inference are less important than bounded traversal, causal edge metadata,
  contradiction tracking, and fast operational summaries.

### Graph-Engineering Principles (Shift A/B/C)

Adopted 2026-08-23 from a graph-engineering review (a real-world case study of a
multi-agent commercial-analytics system that failed under distributed judgment
and was rebuilt around a graph control plane). These three shifts are the
doctrine the rest of this section's schema, compiler loop, and Decision
Ownership table exist to implement — treat them as the standard to evaluate
any future agent-architecture change against, not just historical context.

**Shift A — separate deterministic pre-processing from agentic work.**
Signal detection, threshold monitoring, anomaly identification, and
prioritization are handled by a standard deterministic pipeline before any
LLM is invoked. Signals are pushed to a queue; the agent wakes up to
*investigate*, never to *detect*. In this codebase: `perceive.py`, `vet`
(`plan_vetter.py`), and `evaluate` (`evaluator.py`) must never invoke an LLM —
only `resolve` (`goal_resolver.py`) and `plan` (`plan_generator.py`) may, and
only when a deterministic gate (ambiguity, low confidence, sustained
no-progress) decides escalation is warranted. A196/A197 (below) made this
measurable instead of aspirational.

**Shift B — consolidate reasoning into a single core agent.** Eliminate
distributed judgment across autonomous peers. A single primary agent owns
end-to-end reasoning and decision logic; short-lived sub-agents are spawned
only for isolated, bounded data-gathering tasks and return raw results, never
independent conclusions, to the primary agent. **Honest status in this
codebase (audited 2026-08-23, see below): no single agent owns this today.**
`WorkflowOrchestrator` explicitly does not reason (see the v2 Layer Model
table — "Routes phases, enforces gates"). `goal_resolver.py` and
`plan_generator.py` each own a separate, bounded LLM escalation tier and
never see each other's reasoning, only structured state
(`ResolvedGoal` → candidate list) — which is the *correct* shape for "bounded
sub-agents returning raw results," not a violation. But nothing in the
runtime watches a full episode's trajectory and reasons holistically about
strategy; decisions are a scatter of phase-scoped deterministic gates
(`_action_space_exhausted`, `force_explore_after`, `replan_passes`) each
reading a narrow slice of `WorkflowState`. A194 (graph-driven termination) is
the one piece that asks a holistic-ish question ("are there untested
hypotheses left") — it's a single signal, not strategic reasoning over a run.
This is a real, named gap, not a solved problem — the graph is the intended
substrate of that continuity per this document's own mission statement, but
today it is queried reactively, per-candidate, never consulted as "here's the
whole episode so far, what's the strategy."

**Shift C — the knowledge graph as a control plane, not just RAG.** Domain
entities, relationships, and causal hierarchies are mapped into a structured
graph that is not a passive retrieval database — it functions as the agent's
control plane and search graph. Every graph edge represents an explicit
testable hypothesis. The graph bounds the permissible paths the agent can
explore, guiding investigation from *where* an issue occurs to *why*. This is
this document's own "graph decides, LLM advises" principle stated by another
name — see Decision Ownership below for how it's assigned per layer, and
A191/A192 (candidate exclusion at construction, entity-neighborhood seeding)
for concrete cases of moving from "graph scores an option" to "graph excludes
or admits an option."

#### The Graph-Guided Investigation Loop

1. **Anchor on Entity** — the agent begins at the entity/object of interest
   (in this codebase: A175's stable, cross-frame `entity_ref`).
2. **Inspect Neighborhood** — retrieve adjacent nodes and outgoing edges from
   the graph for that specific entity (A192/A199: `fetch_entity_neighborhood`,
   `ENTITY_HYPOTHESIS` and `ENTITY_RULE` edges — hippocampy repo, B359).
3. **Form & Test Hypotheses** — each edge is a specific, falsifiable causal
   claim (A177's Rule/PREDICTS/FALSIFIED_BY objects).
4. **Evaluate Evidence** — query ground-truth data (observed grid transitions)
   to test the hypothesis against real numbers (A170's before/after diffs,
   `evaluator.py`'s comparison against `predicted_outcome`).
5. **Support or Contradict** — a contradicted branch is discarded (A182,
   A185, A187, A191: falsification is authoritative and excludes the
   candidate outright, not just penalizes it); a supported branch is
   traversed deeper (A199: a confirmed rule on one click of an entity boosts
   that same entity's score on its next consideration).
6. **Terminate on Root Cause** — the loop repeats until all candidate
   hypotheses are evaluated and exhausted (A194: termination is graph-aware,
   not a flat attempt-count threshold — though see Shift B above for the
   remaining gap in *strategic*, not just terminal, reasoning).

Cards A190-A199 are the concrete implementation record for this section —
see the Implementation Track entry below and `backlog/A193.md` for the full
sequence, its two-group split (structural hardening vs. compliance
measurement), and dependency ordering.

### Two-Level Graph Memory

#### 1. Per-Game World Graph

The per-game graph is rebuilt or updated during each live game. It represents
the current game belief state, not a verbose replay log.

Starter schema:

```text
(:Game {id})
(:State {hash, step})
(:Action {id, kind})
(:Observation {step, frame_hash, reward, terminal_score})
(:Effect {kind, magnitude})
(:Object {signature, role})
(:Hypothesis {claim, confidence, status})
(:Mechanic {name, confidence})
(:GoalModel {type, confidence})

(:Game)-[:HAS_STATE]->(:State)
(:State)-[:ACTION_TAKEN {step}]->(:Action)
(:Action)-[:CAUSED {confidence}]->(:Effect)
(:Effect)-[:OBSERVED_IN]->(:Observation)
(:Effect)-[:SUPPORTS]->(:Hypothesis)
(:Effect)-[:CONTRADICTS]->(:Hypothesis)
(:Hypothesis)-[:EXPLAINS]->(:Mechanic)
(:Mechanic)-[:PREDICTS]->(:Effect)
(:Object)-[:MOVED|EXPANDED|BLOCKED|APPROACHED_GOAL]->(:Object)
(:Game)-[:CURRENT_GOAL_MODEL]->(:GoalModel)
```

The per-game graph should answer:

- What do we believe this game is?
- Which actions have causal power?
- Which objects matter?
- What has been falsified?
- What strategy should be tried next?

It should not merely answer "what happened on step 17?"

#### 2. Aggregate Mechanic Graph

The aggregate graph is cross-game memory. It stores reusable mechanic
knowledge: action patterns, effect patterns, preconditions, failure modes,
recovery policies, and plan templates.

Starter schema:

```text
(:Mechanic {name})
(:ActionPattern {signature})
(:EffectPattern {signature})
(:GameArchetype {name})
(:FailureMode {name})
(:RecoveryPolicy {name})
(:PlanTemplate {name})

(:Mechanic)-[:HAS_ACTION_PATTERN]->(:ActionPattern)
(:Mechanic)-[:CAUSES_EFFECT_PATTERN]->(:EffectPattern)
(:Mechanic)-[:APPEARS_IN]->(:GameArchetype)
(:FailureMode)-[:RECOVERED_BY]->(:RecoveryPolicy)
(:Mechanic)-[:USES_PLAN]->(:PlanTemplate)
(:Game)-[:MATCHED_MECHANIC {confidence}]->(:Mechanic)
(:Game)-[:FAILED_BY {evidence}]->(:FailureMode)
```

Aggregate retrieval should return similar world-model structures, not similar
raw logs. For example:

```cypher
MATCH (g:Game {id: $current})-[:HAS_MECHANIC_CANDIDATE]->(m:Mechanic)
MATCH (m)-[:USES_PLAN]->(p:PlanTemplate)
MATCH (m)-[:CAUSES_EFFECT_PATTERN]->(e:EffectPattern)
RETURN m, p, e
ORDER BY m.confidence DESC
LIMIT 5
```

### World-Model Compiler Loop

After each small batch of experiments, the agent should update the world model
instead of starting another generic reasoning cycle.

```text
local observations
-> action-effect table
-> hypothesis update
-> mechanic candidates
-> goal model
-> next experiment
```

For a single-action smoke where `ACTION6` changes pixels but produces no
terminal or object progress, the compiler should produce a model like:

- available action set: single-action
- `ACTION6`: deterministic/churn-producing
- terminal progress: flat or regressing
- object progress: absent
- coordinate relevance: none
- macro eligibility: false productive macro, true cheap-classification probe
- failure risk: `single_action_terminal_stall`

The next policy should be:

- stop the full LLM/MCP loop
- run bounded cheap probes
- classify the mechanic
- if no terminal evidence appears, terminate early or escalate to
  `hidden_precondition_or_world_model_missing`

The agent should not continue paying for full reasoning while pressing the
same non-terminal-relevant action.

### Decision Ownership

| Layer | Responsibility |
|---|---|
| LLM reasoning | Generate hypotheses, propose bounded experiments, explain surprising evidence |
| World-model compiler | Convert telemetry into causal claims, relevance facts, and contradictions |
| Per-game graph | Decide current beliefs, falsifications, goal model, and action relevance |
| Aggregate mechanic graph | Transfer reusable mechanics, failure modes, and recovery policies across games |
| Reasoning controller | Decide whether reasoning is worth paying for now |
| Planner | Choose the next experiment from graph-backed evidence |

### Non-Goals

- Do not call memory more often just because the model is graph-backed.
- Do not store more raw text when a causal summary is enough.
- Do not put blocking memory reads in execute or macro phases.
- Do not let aggregate mechanic memory override current-game evidence.
- Do not import HippoCampy/Campy internals into ARC runtime; all persistent memory
  integration still goes through the MCP seam.

### Implementation Track

The strategic backlog sequence for this architecture is:

- A073: Per-game world model graph
- A074: World-model compiler from step telemetry
- A075: Aggregate mechanic memory
- A076: Evidence-gated reasoning controller
- A077: World-model-guided planner
- A078: World-model evaluation harness and live stream

The currently landed executable prototype for that direction is the ARC v2 runtime slice:

- A118: ARC v2 workflow contracts and orchestrator
- A119: ARC v2 perceive and executor modules
- A120: ARC v2 goal resolver
- A121: ARC v2 plan generator and plan vetter
- A122: ARC v2 evaluator
- A123: `run_single_puzzle.py` integration, telemetry, and smoke path
- B278: ARC-specific MCP query tool surface in the sibling `hippocampy` repo. Campy owns these brain internals (ecosystem-rules.md:47); ARC consumes them across the MCP seam via `agents/arc4/graph_queries.py`. A146 is the consumer-side verification that the A135/A138 evidence loop closes against B278 — closed 2026-08-04: hippocampy's `falsified_count` persistence bug (`docs/handoff/B278-graph-evidence.md`) no longer reproduces after a systematic-debugging pass (2026-08-03), re-verified from ARC's side against a live daemon.

Graph-control-plane compliance hardening and measurement (2026-08-23 graph-engineering
review, see `backlog/A193.md` for full context and ordering):

- A190: `book_id` as a first-class `PlanCandidate` field (structural fix, replacing
  six independently-reimplemented metadata resolutions)
- A191: exclude repeatedly-falsified `book_id`s from the candidate set at construction
- A192: seed candidate generation from entity-neighborhood graph evidence
  (companion hippocampy tool tracked as B359 in the sibling repo)
- A194: make termination graph-aware instead of a flat attempt counter
- A195: assert the Shift-B invariant (no executed candidate is repeatedly-falsified)
  on real run data, with a pass/fail gate script
- A196: trend Shift-A/Shift-C compliance rates across runs, with a reporting script
- A197: assert deterministic phases never incur LLM token cost, extending A195's
  compliance_checks.py rather than a second parallel mechanism
- A198: persist each compliance report to a JSONL history file so rates can be
  trended over time, not just inspected one run at a time

Ordering: A191 before A195 (the invariant A195 checks is only real once A191 exists);
A192 and A194 before A196 (two of its four metrics have nothing to report otherwise);
A195 before A197 (extends the module A195 introduces); A196 before A198 (extends its
script directly). A190 has no hard dependency on the rest of the sequence.

Trajectory Reasoner (2026-08-23, see
`docs/superpowers/specs/2026-08-23-trajectory-reasoner-design.md` for the full design
and `backlog/A206.md` for sequencing context) — the component that closes the Shift-B
gap named above ("no agent... owns the trajectory"):

- A200: pure investigation-thread state machine (no graph/LLM/I/O)
- A201: hippocampy handoff doc + graph client stubs for investigation threads
- A202: wire the Reasoner into `WorkflowOrchestrator`
- A203: anchor-biasing in `goal_resolver.py`/`plan_generator.py`
- A204: resume/crash-safety — write-ahead cycles, real-observation reconciliation (P0)
- A205: degraded-mode fallback + `AWAITING_LLM` failure handling

Ordering: A200 and A201 are the only safe parallel step (no file overlap, no
dependency on each other). A202 depends on both. A203 depends on A202. A204 and A205
both depend on A202 and have no logical dependency on each other, but are sequenced
(A204 then A205) rather than run in parallel because both plausibly touch the same
files (`types.py`, `workflow.py`'s Reasoner hook) — same file-conflict-safety
reasoning as A196/A197 in the prior sequence.

### ARC v2 Runtime (Production Default — A127)

The repo contains a modular ARC v2 runtime under `agents/arc4/`. This is the current production implementation path, promoting the v2 prototype to default agent.

**Phase 4 (A127): Promotion complete.** v2 is now the default agent. **A148 (2026-08-02): v1 retired.** `agents/arc3/` was moved to `archive/agents-arc3/`; `--agent-version=v1` no longer exists (v2 is the only supported agent). See `backlog/A148.md` for the decision rationale.

The ARC v2 slice is organized around shared contracts and injected ports:

- `agents/arc4/types.py` defines workflow dataclasses, decisions, and phase result contracts
- `agents/arc4/ports.py` defines protocol boundaries for graph access, optional LLM escalation, and phase callables
- `agents/arc4/workflow.py` owns the thin `WorkflowOrchestrator` over the phase order `PERCEIVE -> RESOLVE -> PLAN -> VET -> EXECUTE -> EVALUATE`
- `agents/arc4/perceive.py`, `goal_resolver.py`, `plan_generator.py`, `plan_vetter.py`, `executor.py`, and `evaluator.py` implement the phase logic behind those contracts
- `agents/arc4/graph_queries.py` adapts ARC runtime calls onto the ARC-specific MCP tool surface exposed by the sibling `hippocampy` repo
- `agents/arc4/telemetry.py` converts workflow events into smoke/live artifact rows

#### v2 Layer Model

| Layer | Module | Brain Region | Role |
|---|---|---|---|
| **1. Orchestrator** | `agents/arc4/workflow.py` | Thalamus | Routes phases, enforces gates. Does not reason. |
| **2. Perceive** | `agents/arc4/perceive.py` | Sensory Cortex | Observation → structured perception (GridSnapshot, entities) |
| **2. Goal Resolve** | `agents/arc4/goal_resolver.py` | Temporal Lobe | Tiered goal system (heuristic → graph → LLM) |
| **2. Plan Generate** | `agents/arc4/plan_generator.py` | Prefrontal Cortex | Goal-conditioned action planning (LLM) |
| **2. Plan Vet** | `agents/arc4/plan_vetter.py` | Basal Ganglia | Go/No-Go advisory gate (deterministic) |
| **2. Execute** | `agents/arc4/executor.py` | Motor Cortex | Action execution (no LLM, no graph) |
| **2. Evaluate** | `agents/arc4/evaluator.py` | Temporal Lobe | Adversarial post-action review (deterministic) |
| **3. Tools** | inline within `plan_generator.py`/`evaluator.py` | — | Deterministic scoring, candidate ranking (no arc3 dependency since A144) |
| **4. Memory** | HippoCampy via MCP | Hippocampus + Basal Ganglia | 15 `arc_*` query tools (B278) |

**Design principles:**
1. The graph decides, the LLM advises
2. MCP seam only — all memory access through MCP
3. No module grades its own work — PlanVetter and Evaluator are independent
4. Deterministic where possible, LLM where needed

**v1 retirement (A148, 2026-08-02):** The v1 agent (`agents/arc3/`, including `orchestrator.py`) was moved to `archive/agents-arc3/` along with its dedicated test suite and the v1-vs-v2 comparison harness. It is not runnable via the CLI anymore — kept for git history/reference only. See `archive/agents-arc3/README.md`.

**`agents/arc4/temporal_workflows.py` — deprecated (2026-08-23), not deleted.** `ArcPuzzleWorkflow` was a Temporal.io-backed execution path (opt-in via `--temporal` / `ARC_TEMPORAL_ENABLED=1`, never the default), intended to be the durable orchestration layer for trajectory-level decisions. On inspection it never became that: it's a mechanical port of the exact same fixed phase sequence and the same narrow gates `WorkflowOrchestrator` already runs, with zero reasoning added — Temporal's replay/retry machinery, not decision-making. The Reasoner design being worked out for Shift B (see Decision Ownership and the Graph-Engineering Principles section above) puts durability and decision authority in the graph itself, not in Temporal's per-workflow event history — a fresh process resumes by querying the graph for an attempt's in-progress state, not by Temporal replay, and the one case that requires special care (a non-idempotent side effect against the real ARC API, e.g. a click, in flight when a crash happens) is resolved by checking the real observation/game state on resume, not by trusting either store's own bookkeeping. `temporal_workflows.py` is not being built on or extended by that work. Left in place, unused, not wired into the new design — a separate future decision whether to remove it.

### ARC v2 MCP Rollout Status

ARC v2 is designed to use ARC-specific MCP tools provided by the sibling `hippocampy` repo. During rollout, the runtime must tolerate a server that has not yet exposed the new `arc_*` methods.

Current policy:

- runtime ARC v2 uses non-strict MCP graph access and degrades gracefully when ARC-specific tools are unavailable
- missing capability placeholders must not be treated as real graph evidence
- optional LLM escalation must fail closed rather than crash the workflow
- the production dependency boundary is unchanged: runtime code still talks to memory through the MCP seam only

## Relationship To HippoCampy / Campy

The architectural split is:

1. `hippocampy` / Campy provides persistent local memory, retrieval, graph storage, and MCP-oriented tooling
2. `ARC_AGI` provides the puzzle-solving agent, evaluation harness, and ARC-specific orchestration

That means `ARC_AGI` should depend on HippoCampy/Campy, not absorb it.

### Current Dependency Boundary

`ARC_AGI` production code now uses an ARC-owned MCP client seam:

- `sidequest_mcp_client/mcp_session.py`
- `sidequest_mcp_client/mcp_brain_client.py`
- `sidequest_mcp_client/readiness.py`
- `sidequest_mcp_client/observability.py`

That seam talks to HippoCampy/Campy through the generic stdio MCP adapter:

- `python -m campy.adapters.mcp_server`

Production ARC code should not directly import `mcp_engine.*` or `campy.*` / `sidequests.*`.
Any compatibility helpers that still rely on direct imports must live under
`sidequest_mcp_client/test_compat/` and stay out of production call paths.

So the boundary is no longer the earlier in-process wrapper design. The repo now
has a real MCP client seam in production, with any direct-import escape hatches
isolated to test-only support.

### Target Dependency Boundary

Longer term, `ARC_AGI` should depend on one of these narrower surfaces:

- a published `hippocampy` package API
- a dedicated client/SDK layer for memory access
- MCP/tool calls only, with no direct import of HippoCampy/Campy internals

The desired end state is:

- `ARC_AGI` owns ARC behavior
- HippoCampy/Campy owns memory behavior
- integration happens through a stable, documented interface

### MCP v1 — stdio-only production seam (runtime scope)

The MCP stdio seam policy applies to the interactive runtime path — `agents/` (now just `agents/arc4/` since A148 retired `agents/arc3/`), `arc_runtime/`, `run_single_puzzle.py`, and `sidequest_mcp_client/`. Offline scoring and submission packaging under `benchmarks/arc3/` embed the brain directly (Kuzu client, schema init, loop queue, centroids) and are exempt from the seam policy, because submission packages cannot depend on a running MCP subprocess. The import-boundary test (`tests/test_import_boundary.py`) enforces the runtime scope; `benchmarks/` is not in its `PROD_PATHS` list (A030).



For v1, the canonical production seam between `ARC_AGI` and HippoCampy/Campy is MCP
over stdio only. Production ARC components interact with HippoCampy/Campy through the
ARC-owned client package `sidequest_mcp_client/`.

Allowed production seam:

- `sidequest_mcp_client.mcp_session`
- `sidequest_mcp_client.mcp_brain_client`
- `sidequest_mcp_client.readiness`
- `sidequest_mcp_client.observability`

Not allowed in production:

- direct `mcp_engine.*` imports
- direct `campy.*` / `sidequests.*` imports
- `sidequest_mcp_client/test_compat/*`

ARC-side client responsibilities (v1)

- Initialize session: create a client connection to the MCP stdio endpoint, perform handshake/capability negotiation, and expose a `ready` indicator before any tool calls.
- List tools: discover available tools with names, schemas, and metadata.
- Call tools: invoke tools by canonical name with a structured args envelope; receive a structured result envelope with status and payload.
- Normalize: enforce a canonical request/response JSON envelope for all tool calls to ensure stable parsing and provenance.
- Failure handling: categorize errors (transient, permanent, validation), enforce timeouts, retry/backoff policy, idempotency keys, and safe fallback behavior if memory services are unavailable.
- Tool-specific timeout budgeting: expensive memory operations such as `current_truth`, `register_plan`, `notify_turn`, and `upsert_lesson` may use larger budgets than lighter MCP calls.

Session lifecycle and startup/readiness expectations

- Startup: on process start, ARC clients must connect and perform a handshake; callers must wait for the client `ready` signal before issuing operations.
- Session scope: sessions may be reused across episodes or scoped per worker — the client implementation should document lifetime semantics and resource cleanup procedures.
- Shutdown: expose graceful close semantics to allow HippoCampy/Campy to flush state and release resources.
- Observability: the client should emit readiness, last-activity, and error metrics for operational monitoring.

Canonical ARC-side client interface (recommended)

- `initialize_session(config) -> session_handle` — block/await until ready.
- `list_tools(session_handle) -> list[{name, schema, description}]`
- `call_tool(session_handle, tool_name, args, timeout=None) -> result_envelope`
- convenience wrappers for memory operations: `notify_turn`, `current_truth`, `recall_plans`, `recall_relevant_lessons`, `analogical_search`, `register_plan`, `report_outcome`, `upsert_lesson`, `recall_procedures`, `get_knowledge_gaps`

Policy statement

Production ARC code MUST NOT directly import HippoCampy/Campy internals (for example
`mcp_engine.*`); instead it must use the documented MCP stdio client contract
above. If a test still needs direct-import compatibility, that helper must live
under `sidequest_mcp_client/test_compat/` and stay out of production call paths.

Adapter ownership

The MCP stdio adapter that serves this seam — the binary `CAMPY_MCP_CMD`
points at — lives in `hippocampy/campy/adapters/mcp_server.py`, not
in this repo. It is a brain-side artifact: it imports the unix-socket path,
offline-queue format, and git-context detection from the `campy` package,
and bridges MCP stdio JSON-RPC to the brain daemon at `~/.campy/brain.sock`.
`ARC_AGI` must not vendor or reimplement it. Other MCP clients (Smithery,
Claude Desktop, Cursor) connect to the same adapter through their own
`CAMPY_MCP_CMD`-equivalent configuration.

Operator setup

From inside `ARC_AGI/`, point the env var at the sibling repo's venv + adapter:

```bash
export CAMPY_MCP_CMD="../hippocampy/.venv/bin/python -m campy.adapters.mcp_server"
```

The brain daemon (socket at `~/.campy/brain.sock`, with legacy `~/.sidequests/brain.sock` fallback) must already be
running. `check_mcp_readiness` starts the adapter as a subprocess, performs
the MCP `initialize` + `tools/list` handshake, and fails fast with a
`ReadinessError` if the adapter or the brain is unavailable.

## System Overview

```
ARC_AGI Repo
  ├── ARC solver/orchestrator
  ├── ARC benchmark harness
  ├── evaluation + compliance tooling
  └── Campy integration layer
          └── uses HippoCampy/Campy memory services
```

### Runtime Shape

```
ARC environment / task source
  -> ARC harness
  -> ARC orchestrator
  -> ARC strategy / solve engine
  -> Campy-backed brain client
  -> Campy local memory graph
  -> retrieval / plans / lessons / outcome learning
```

## Major Components

### `archive/agents-arc3/` (formerly `agents/arc3/`, retired A148)

The v1 ARC agent — orchestrator, solver, hypothesis management, and supporting modules
(`grid_analysis.py`, `entity_graph.py`, `supervisor.py`, `circuit_breaker.py`, `cost_tracker.py`,
`scheduler.py`, `strategy_racer.py`, `checkpoint.py`, and others). Not part of the active runtime —
see `archive/agents-arc3/README.md` for what moved and why. `agents/arc4/` is the current agent;
it has no dependency on this archive (enforced by `tests/test_import_boundary.py`).

### `benchmarks/arc3/`

ARC-specific execution, evaluation, and packaging.

- `harness.py`
  baseline versus Campy-augmented evaluation path
- `adapter.py`
  bridge between ARC episodes and Campy-style brain calls
- `schema.py`
  ARC observation/action data contracts
- `state_serializer.py`
  state-to-text conversion for memory and prompting
- `submission.py` — **moved to `archive/agents-arc3/submission.py` (A148).** It was a v1-only
  submission runner (hardcoded to `DurableARCRunner`), not referenced by any Makefile target or
  used by v2. Everything else in this directory is unrelated to `agents/arc3` and stays active.
- `model_eval.py`
  prompt/model comparison tooling
- `outcome_judge.py`
  rubric-style grading for near-miss trajectories
- `trajectory_eval.py`
  trajectory-quality scoring
- `regression_monitor.py`
  cross-run regression tracking
- `pre_submit_check.py`, `package_offline_assets.py`, `verify_offline_bundle.py`
  packaging/compliance utilities

## Cognitive Model

The ARC agent uses a durable, inspectable phase-state machine:

1. `PERCEIVE`
2. `MODEL`
3. `HYPOTHESIZE`
4. `ROUTE`
5. `EXECUTE`
6. `EVALUATE`
7. `REPLAN`

This loop is ARC-owned. Memory persistence is Campy-owned.

`REPLAN` is a first-class recovery/escalation phase rather than an implicit
fallback. The runtime can now route back into better modeling or strategy
selection instead of treating every stall as a generic crash.

#### Route-reason taxonomy

- `low_value_but_known_geometry` → all tested actions are low_value AND player/goal confidences ≥ 0.6 → resume at MODEL to reconsider archetype given the geometry
- `signature_escalation` → identical REPLAN signature seen back-to-back → escalate to MODEL
- `exploration_incomplete` → action_coverage.initial_exploration_complete is False → stay in MODEL to keep exploring
- `low_archetype_conf` → archetype_confidence < 0.3 → drop to HYPOTHESIZE
- `rebuild_route_from_saturation` → coverage saturated and geometry known → ROUTE (A010 has already graduated the chunker)
- `default` → no evidence gate fired → ROUTE

A011 covers only the orchestrator-side `register_plan`. The solver has two
additional register paths (`_register_chunk_plan`, `_register_solve_plan`) which
A024 extends with the same fingerprint semantics:
`(plan_type, goal, tuple(steps), archetype, vc_type, chunk_desc_or_None)`.
Chunk descriptions are normalized (trailing "(step N)" parentheticals are
stripped) before entering the fingerprint so that cosmetic step-ordinal
rewording does not defeat dedup.

### Phase 1: Exploration / Modeling

Goal: learn what the puzzle environment does before overcommitting to a solve theory.

Exploration includes:

- state transitions
- action semantics
- invariants and stable regions
- object/group behavior
- candidate action facts

Primary outputs:

- action facts
- path hypotheses
- role hints
- structural summaries
- failure evidence for later retrieval

#### Exploration-coverage policy (A023)

The orchestrator enforces a proactive exploration guard before the LLM's
ranking and the B209 route-execute contract run. When two consecutive steps
have produced no reward AND at least one action in `available_actions` has
never been tried, the next action is forced to the alphabetically-first
untested candidate. The guard yields to `autopilot` and `plateau_override`
decision sources, and it does not fire when the active chunk already calls
for an untested action next. It emits `guard_untested_probe` and
`exploration_coverage_snapshot` trace events for auditability.

The `exploration_coverage_snapshot` event fires once per step at PERCEIVE
phase entry, guarded by `_last_coverage_snapshot_step` to prevent duplicate
emission within a step (A025).

### Phase 2: Goal-Directed Solving

Goal: turn exploration evidence into a goal-directed policy.

Solve-time responsibilities include:
Primary outputs:

- archetype classification
- object role assignment
- victory-condition inference
- chunk generation
- dissonance/stall detection
- replanning when the current theory stops making progress

#### Plateau family memory

The solver keeps a set `_failed_plateau_families` across an entire solve()
call. A family enters this set only via the plateau-exhaustion guard
(two consecutive no-progress replans on the same locked family). The set
is cleared only by a reward tick or a full solver reset — never by cell
changes alone. Lock selection subtracts this set from the candidate pool,
and if two or more families have failed and no unfailed candidate
remains, the solver raises `plateau_escalation_required` which the
orchestrator translates to `COVERAGE_SATURATED_ABORT` when the
action-coverage signal also agrees.

## How ARC Uses Campy Memory
The ARC stack treats Campy as a memory substrate, not as solver logic.

### Core Memory Operations Used

- `notify_turn`
  ingest observations, actions, and state narratives
- `current_truth`
  retrieve current relevant memory
- `recall_plans`
  reuse goal/strategy templates
- `recall_relevant_lessons`
  bring back prior successful or failed lessons
- `analogical_search`
  retrieve structurally similar prior situations
- `register_plan`
  persist declared strategy/chunk plans
- `report_outcome`
  write success/failure/valence back to memory
- `upsert_lesson`
  persist durable lessons
- `recall_procedures`
  pull reusable procedure-like patterns
- `get_knowledge_gaps`
  surface unresolved missing understanding
- task-graph tools such as `register_task_graph`, `get_ready_tasks`, `advance_task`, `fail_task`, `get_task_graph`
  support batch/task orchestration when enabled

### Runtime Notes

- production startup uses MCP readiness checks instead of directly bootstrapping
  Campy graph/schema internals
- `run_single_puzzle.py` now performs fail-fast preflight for:
  - LLM initialization
  - observability initialization
  - HippoCampy MCP readiness
- local `provider=ollama` still uses the OpenAI-compatible Python SDK in this
  repo architecture, so the `openai` package is a real runtime dependency
- timeout attribution distinguishes MCP/tool stalls from true LLM timeouts so
  benchmark outputs point at the correct subsystem

### Observability defaults

- default project: `arc-agi-campy`
- default endpoint: `http://127.0.0.1:6006/v1/traces`
- auto-enabled in `run_single_puzzle.py` when `opentelemetry`, `phoenix`, and `phoenix.otel` are all importable
- disable with `[observability] enabled = false` in `campy.toml` or `~/.campy/config.toml` (legacy `sidequests.toml` / `~/.sidequests/config.toml` still supported)

Note: Phoenix auto-enable is best-effort in the default auto-enable path (A022); when unavailable the runtime falls back to the JSON trace as the primary diagnostic surface. See `docs/trace_recipes.md` for canonical jq recipes to analyze `agent_execution_trace.json` and related artifacts.
- override project with `PHOENIX_PROJECT=<name>` environment variable
- override endpoint with `PHOENIX_ENDPOINT=<url>` environment variable

### Why This Matters

The ARC agent should not need to remember everything inside its prompt.
It should be able to offload durable state into HippoCampy/Campy and retrieve only what is useful for the next decision.

That makes `ARC_AGI` a good consumer of the memory system, but not the owner of memory-system architecture.

## Repository Structure

```
ARC_AGI/
├── ARCHITECTURE.md
├── README.md
├── pyproject.toml
├── run_single_puzzle.py
├── sidequest_mcp_client/
│   ├── mcp_session.py
│   ├── mcp_brain_client.py
│   ├── readiness.py
│   ├── observability.py
│   └── test_compat/
├── arc_runtime/
│   ├── config.py
│   └── llm.py
├── agents/
│   └── arc4/
├── benchmarks/
│   ├── __init__.py
│   ├── ab_harness.py
│   ├── harness.py
│   └── arc3/
├── archive/
│   └── agents-arc3/       # retired v1 agent (A148); not part of the runtime
└── tests/
```

## Current Operational Status

The architectural split is now real:

- `ARC_AGI` owns solver, harness, evaluation, and ARC runtime behavior
- `hippocampy` / Campy owns durable memory, retrieval, graph storage, and MCP tool implementation
- production integration is MCP over stdio, not direct import

Recent stabilization work also made the runtime more honest:

- MCP/tool timeouts are classified separately from LLM timeouts
- expensive MCP calls have explicit per-tool timeout budgets
- local LLM startup fails early with actionable messages when runtime dependencies are missing

That means the doc should be read as a description of the current production
boundary, not the earlier extraction-in-progress state.

## Current Extraction Status

The repo split is clean at both the folder level and the interface level (A002-A006).

### Already Separated

- ARC code has its own sibling repo/workspace
- ARC docs now have their own canonical architecture file
- ARC tests live with ARC code
- packaging metadata is separate
- production runtime integration is MCP over stdio; direct `mcp_engine.*` / `campy.*` / `sidequests.*` imports are forbidden in production paths by `BacklogRules.md` rule 4 and enforced by `tests/test_import_boundary.py`

### Validation Baseline

The full-suite baseline (`make test-all`) is the required regression signal for A-card work, with `make test-a` retained as a fast subset. The broader `pytest -q` suite was restored through the A029 follow-up sequence (A030-A037); A037 records the full suite at 723/723 passing while preserving the MCP seam import boundary.

## Recommended Next Steps

1. Keep ARC docs and benchmarks evolving in this sibling repo only.
2. Keep `hippocampy` / Campy architecture focused on memory-system responsibilities only.

## Non-Goals

`ARC_AGI` should not become:

- a second copy of Campy
- the canonical home of memory schema design
- the place where Campy product direction is decided

Its role is solver experimentation and benchmark execution.
