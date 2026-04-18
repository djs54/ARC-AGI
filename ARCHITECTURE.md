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

Today, `ARC_AGI` still imports SideQuests internals directly:

- `mcp_engine.config`
- `mcp_engine.graph.kuzu_client`
- `mcp_engine.schema`
- `mcp_engine.tools`
- `mcp_engine.observability`
- `mcp_engine.llm.provider`
- loop preload helpers like `step2_gist` and `step3_schema_org`

So the split is structurally cleaner now, but the runtime boundary is still tighter than ideal.

ARC_AGI now contains a small `sidequest_mcp_client/` package that serves as the MCP-facing seam for production code. Any direct-import compatibility helpers belong under `sidequest_mcp_client/test_compat/` and are not part of the production boundary.
Important: this bridge is currently an in-process import wrapper, not an MCP client calling SideQuests over MCP endpoints.

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

For v1, the canonical production seam between `ARC_AGI` and SideQuests is MCP over stdio only. Production ARC components must interact with SideQuests via an MCP client using a stdio transport (local process or managed service). In the current repo, the allowed production seam is the MCP-facing portion of `sidequest_mcp_client/` (`mcp_session`, `mcp_brain_client`, `readiness`, `observability`). Direct-import compatibility helpers are isolated under `sidequest_mcp_client/test_compat/` and are not allowed in production code.

ARC-side client responsibilities (v1)

- Initialize session: create a client connection to the MCP stdio endpoint, perform handshake/capability negotiation, and expose a `ready` indicator before any tool calls.
- List tools: discover available tools with names, schemas, and metadata.
- Call tools: invoke tools by canonical name with a structured args envelope; receive a structured result envelope with status and payload.
- Normalize: enforce a canonical request/response JSON envelope for all tool calls to ensure stable parsing and provenance.
- Failure handling: categorize errors (transient, permanent, validation), enforce timeouts, retry/backoff policy, idempotency keys, and safe fallback behavior if memory services are unavailable.

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

Production ARC code MUST NOT directly import SideQuests internals (for example `mcp_engine.*`); instead it must use the documented MCP stdio client contract above. If a test still needs direct-import compatibility, that helper must live under `sidequest_mcp_client/test_compat/` and stay out of production call paths.

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

The ARC agent uses a staged loop:

1. Perceive
2. Hypothesize
3. Solve
4. Plan
5. Act
6. Evaluate

This loop is ARC-owned. Memory persistence is SideQuests-owned.

### Phase 1: Exploration

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

### Phase 2: Solving

Goal: turn exploration evidence into a goal-directed policy.

Solve-time responsibilities include:

- archetype classification
- object role assignment
- victory-condition inference
- chunk generation
- dissonance/stall detection
- replanning when the current theory stops making progress

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
├── agents/
│   └── arc3/
├── benchmarks/
│   ├── __init__.py
│   ├── ab_harness.py
│   ├── harness.py
│   └── arc3/
└── tests/
```

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
