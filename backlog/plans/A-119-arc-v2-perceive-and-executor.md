# Plan: A-119 — ARC v2 Perceive and Executor Modules

## Card metadata

- **Card:** A119
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A118
- **Intended executor:** GPT-5.4-mini subagent

## Summary

Implement the deterministic bookend modules for ARC v2 using only A118 contracts. This card should be safe to build in parallel with A120 once A118 lands.

## Parallel contract

- Import shared shapes only from `agents.arc4.types` and `agents.arc4.ports`.
- Do not import `goal_resolver`, `plan_generator`, `plan_vetter`, `evaluator`, or `graph_queries`.
- Do not import `MCPBrainClient` directly. Use the injected graph-query port from A118.

## Implementation approach

### Step 1: Implement `agents/arc4/perceive.py`

Responsibilities:

- accept the current raw observation plus workflow state
- compute or normalize a stable grid hash
- extract entities and compact structural features using existing ARC helpers where practical
- detect repeated-state loops using workflow state rather than module globals
- emit the A118 `PerceptionSnapshot`
- optionally perform a best-effort graph ingestion call through the query port

Non-goals:

- no goal inference
- no ranking or action choice
- no telemetry formatting

### Step 2: Implement `agents/arc4/executor.py`

Responsibilities:

- accept an approved `PlanCandidate`, game context, and workflow state
- execute the selected action against the existing harness or runner surface
- normalize the returned observation into the A118 execution-result contract
- surface transport or harness failures as structured execution failures instead of ad hoc exceptions when possible

Non-goals:

- no graph reads or writes
- no LLM calls
- no veto logic

### Step 3: Add focused tests

Create `tests/test_arc4_perceive_executor.py` covering:

- stable grid hash generation
- repeated hash loop detection
- perception graph write being best-effort rather than fatal
- executor success path
- executor error normalization when harness/session is missing or fails

## Concrete file edits

- `agents/arc4/perceive.py`
- `agents/arc4/executor.py`
- `tests/test_arc4_perceive_executor.py`

## Interface requirements

- `PerceiveAgent` returns the A118 `PerceptionSnapshot` shape exactly.
- `Executor` consumes an A118 `PlanCandidate` and returns the A118 execution-result shape exactly.
- Any graph interaction in perception goes through one injected query-port method intended for state ingestion.

## Validation commands

```bash
pytest -q tests/test_arc4_perceive_executor.py
```

## Assumptions and defaults

- Existing ARC helpers in `agents/arc3/` may be reused if they do not pull in unrelated orchestration logic.
- Best-effort graph ingestion is allowed to no-op when the injected query port is absent in unit tests.