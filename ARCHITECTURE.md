# ARC_AGI Architecture

> Canonical architecture reference for the `ARC_AGI` sibling repo.
> This document covers the ARC solver, harness, and the dependency boundary to SideQuests/Campy.

## Mission

`ARC_AGI` is the benchmark and solver repo.

Its job is to:

- run ARC-AGI experiments
- host the ARC solver/orchestration logic
- evaluate strategy, prompt shape, and runtime behavior
- use SideQuests/Campy as the external local-memory substrate

It is not the memory engine.

## Relationship To SideQuests / Campy

The architectural split is:

1. `sidequests-brain` / Campy provides persistent local memory, retrieval, graph storage, and MCP-oriented tooling
2. `ARC_AGI` provides the puzzle-solving agent, evaluation harness, and ARC-specific orchestration

That means `ARC_AGI` should depend on SideQuests, not absorb it.

### Current Dependency Boundary

`ARC_AGI` production code now uses an ARC-owned MCP client seam:

- `sidequest_mcp_client/mcp_session.py`
- `sidequest_mcp_client/mcp_brain_client.py`
- `sidequest_mcp_client/readiness.py`
- `sidequest_mcp_client/observability.py`

That seam talks to SideQuests through the generic stdio MCP adapter:

- `python -m sidequests.adapters.mcp_server`

Production ARC code should not directly import `mcp_engine.*` or `sidequests.*`.
Any compatibility helpers that still rely on direct imports must live under
`sidequest_mcp_client/test_compat/` and stay out of production call paths.

So the boundary is no longer the earlier in-process wrapper design. The repo now
has a real MCP client seam in production, with any direct-import escape hatches
isolated to test-only support.

### Target Dependency Boundary

Longer term, `ARC_AGI` should depend on one of these narrower surfaces:

- a published `sidequests-brain` package API
- a dedicated client/SDK layer for memory access
- MCP/tool calls only, with no direct import of SideQuests internals

The desired end state is:

- `ARC_AGI` owns ARC behavior
- SideQuests owns memory behavior
- integration happens through a stable, documented interface

### MCP v1 — stdio-only production seam

For v1, the canonical production seam between `ARC_AGI` and SideQuests is MCP
over stdio only. Production ARC components interact with SideQuests through the
ARC-owned client package `sidequest_mcp_client/`.

Allowed production seam:

- `sidequest_mcp_client.mcp_session`
- `sidequest_mcp_client.mcp_brain_client`
- `sidequest_mcp_client.readiness`
- `sidequest_mcp_client.observability`

Not allowed in production:

- direct `mcp_engine.*` imports
- direct `sidequests.*` imports
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
- Shutdown: expose graceful close semantics to allow SideQuests to flush state and release resources.
- Observability: the client should emit readiness, last-activity, and error metrics for operational monitoring.

Canonical ARC-side client interface (recommended)

- `initialize_session(config) -> session_handle` — block/await until ready.
- `list_tools(session_handle) -> list[{name, schema, description}]`
- `call_tool(session_handle, tool_name, args, timeout=None) -> result_envelope`
- convenience wrappers for memory operations: `notify_turn`, `current_truth`, `recall_plans`, `recall_relevant_lessons`, `analogical_search`, `register_plan`, `report_outcome`, `upsert_lesson`, `recall_procedures`, `get_knowledge_gaps`

Policy statement

Production ARC code MUST NOT directly import SideQuests internals (for example
`mcp_engine.*`); instead it must use the documented MCP stdio client contract
above. If a test still needs direct-import compatibility, that helper must live
under `sidequest_mcp_client/test_compat/` and stay out of production call paths.

Adapter ownership

The MCP stdio adapter that serves this seam — the binary `SIDEQUESTS_MCP_CMD`
points at — lives in `sidequests-brain/sidequests/adapters/mcp_server.py`, not
in this repo. It is a brain-side artifact: it imports the unix-socket path,
offline-queue format, and git-context detection from the `sidequests` package,
and bridges MCP stdio JSON-RPC to the brain daemon at `~/.sidequests/brain.sock`.
`ARC_AGI` must not vendor or reimplement it. Other MCP clients (Smithery,
Claude Desktop, Cursor) connect to the same adapter through their own
`SIDEQUESTS_MCP_CMD`-equivalent configuration.

Operator setup

From inside `ARC_AGI/`, point the env var at the sibling repo's venv + adapter:

```bash
export SIDEQUESTS_MCP_CMD="../sidequests-brain/.venv/bin/python ../sidequests-brain/sidequests/adapters/mcp_server.py"
```

The brain daemon (socket at `~/.sidequests/brain.sock`) must already be
running. `check_mcp_readiness` starts the adapter as a subprocess, performs
the MCP `initialize` + `tools/list` handshake, and fails fast with a
`ReadinessError` if the adapter or the brain is unavailable.

## System Overview

```
ARC_AGI Repo
  ├── ARC solver/orchestrator
  ├── ARC benchmark harness
  ├── evaluation + compliance tooling
  └── SideQuests integration layer
          └── uses SideQuests/Campy memory services
```

### Runtime Shape

```
ARC environment / task source
  -> ARC harness
  -> ARC orchestrator
  -> ARC strategy / solve engine
  -> SideQuests-backed brain client
  -> SideQuests local memory graph
  -> retrieval / plans / lessons / outcome learning
```

## Major Components

### `agents/arc3/`

ARC-specific cognition and orchestration.

- `orchestrator.py`
  Main control loop: perceive, hypothesize, solve, plan, act, evaluate
- `solver.py`
  Solve engine, rule hypotheses, object roles, chunking, strategy logic
- `runner.py`
  Durable run driver across tasks/puzzles
- `phase.py`
  Durable phase-state machine with explicit `REPLAN` handling
- `hypothesis.py`
  Hypothesis management and transition/state modeling
- `grid_analysis.py`
  Grid diffing and structural pattern analysis
- `repl_verification.py`
  Replay/refinement verification loops
- `entity_graph.py`
  Graph-style exploration support for puzzle structure
- `supervisor.py`
  Meta-supervision over trajectory quality
- `circuit_breaker.py`
  Failure containment around LLM/tool instability
- `cost_tracker.py`
  Token and cost budget enforcement
- `scheduler.py`
  Puzzle ordering and runtime health logic
- `strategy_racer.py`
  Parallel strategy-variant evaluation
- `checkpoint.py`
  Crash-safe durable checkpointing

### `benchmarks/arc3/`

ARC-specific execution, evaluation, and packaging.

- `harness.py`
  baseline versus SideQuests-augmented evaluation path
- `adapter.py`
  bridge between ARC episodes and SideQuests-style brain calls
- `schema.py`
  ARC observation/action data contracts
- `state_serializer.py`
  state-to-text conversion for memory and prompting
- `submission.py`
  submission/evaluation runner
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

This loop is ARC-owned. Memory persistence is SideQuests-owned.

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

## How ARC Uses SideQuests Memory
The ARC stack treats SideQuests as a memory substrate, not as solver logic.

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
  SideQuests graph/schema internals
- `run_single_puzzle.py` now performs fail-fast preflight for:
  - LLM initialization
  - observability initialization
  - SideQuests MCP readiness
- local `provider=ollama` still uses the OpenAI-compatible Python SDK in this
  repo architecture, so the `openai` package is a real runtime dependency
- timeout attribution distinguishes MCP/tool stalls from true LLM timeouts so
  benchmark outputs point at the correct subsystem

### Observability defaults

- default project: `arc-agi-sidequests`
- default endpoint: `http://127.0.0.1:6006/v1/traces`
- auto-enabled in `run_single_puzzle.py` when `opentelemetry`, `phoenix`, and `phoenix.otel` are all importable
- disable with `[observability] enabled = false` in `sidequests.toml` or `~/.sidequests/config.toml`

Note: Phoenix auto-enable is best-effort in the default auto-enable path (A022); when unavailable the runtime falls back to the JSON trace as the primary diagnostic surface. See `docs/trace_recipes.md` for canonical jq recipes to analyze `agent_execution_trace.json` and related artifacts.
- override project with `PHOENIX_PROJECT=<name>` environment variable
- override endpoint with `PHOENIX_ENDPOINT=<url>` environment variable

### Why This Matters

The ARC agent should not need to remember everything inside its prompt.
It should be able to offload durable state into SideQuests and retrieve only what is useful for the next decision.

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
│   └── arc3/
├── benchmarks/
│   ├── __init__.py
│   ├── ab_harness.py
│   ├── harness.py
│   └── arc3/
└── tests/
```

## Current Operational Status

The architectural split is now real:

- `ARC_AGI` owns solver, harness, evaluation, and ARC runtime behavior
- `sidequests-brain` owns durable memory, retrieval, graph storage, and MCP tool implementation
- production integration is MCP over stdio, not direct import

Recent stabilization work also made the runtime more honest:

- MCP/tool timeouts are classified separately from LLM timeouts
- expensive MCP calls have explicit per-tool timeout budgets
- local LLM startup fails early with actionable messages when runtime dependencies are missing

That means the doc should be read as a description of the current production
boundary, not the earlier extraction-in-progress state.

## Current Extraction Status

The repo split is clean at the folder level, but not yet complete at the interface level.

### Already Separated

- ARC code has its own sibling repo/workspace
- ARC docs now have their own canonical architecture file
- ARC tests live with ARC code
- packaging metadata is separate

### Still Shared Through Imports

- SideQuests config and schema bootstrap
- SideQuests observability utilities
- SideQuests graph client
- SideQuests memory tool handlers
- SideQuests loop preload helpers

## Recommended Next Steps

1. Introduce a narrower SideQuests client boundary for ARC.
2. Stop importing `mcp_engine.*` internals directly from ARC where possible.
3. Keep ARC docs and benchmarks evolving in this sibling repo only.
4. Keep `sidequests-brain` architecture focused on memory-system responsibilities only.

## Non-Goals

`ARC_AGI` should not become:

- a second copy of SideQuests
- the canonical home of memory schema design
- the place where SideQuests product direction is decided

Its role is solver experimentation and benchmark execution.
