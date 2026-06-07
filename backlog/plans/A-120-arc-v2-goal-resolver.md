# Plan: A-120 — ARC v2 Goal Resolver

## Card metadata

- **Card:** A120
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A118
- **Intended executor:** GPT-5.4-mini subagent

## Summary

Implement the tiered ARC v2 goal resolver against the A118 contracts so it can be developed and tested before the real MCP adapter lands.

## Parallel contract

- Import only A118 shared contracts plus existing low-level ARC helpers that operate on local state.
- Treat graph access and LLM access as injected ports.
- Do not import `graph_queries.py`; that adapter belongs to A123.

## Implementation approach

### Step 1: Define the goal-resolution flow

The resolver should run three tiers in order:

1. deterministic heuristic proposals from the current perception
2. graph-evidence merge through the query port
3. optional LLM escalation only when ambiguity thresholds are hit

The output is one resolved-goal payload plus the set of hypotheses considered.

### Step 2: Fix the escalation and grounding rules

Codify these rules in the module and its tests:

- graph evidence must merge into the shared goal hypothesis model rather than replacing it with unrelated dicts
- confidence cannot increase on a step with no meaningful progress
- LLM escalation may trigger only when the top candidates remain ambiguous or confidence remains too low for too long

### Step 3: Keep the module pure from integration concerns

The resolver should not:

- write telemetry files
- know about `run_single_puzzle.py`
- call `MCPBrainClient` directly
- depend on the concrete B278 adapter implementation

### Step 4: Add focused tests

Create `tests/test_arc4_goal_resolver.py` for:

- heuristic hypothesis generation
- graph-evidence merge
- grounding-gate enforcement
- LLM escalation trigger conditions
- fallback behavior when no graph or LLM port is injected

## Concrete file edits

- `agents/arc4/goal_resolver.py`
- `tests/test_arc4_goal_resolver.py`

## Interface requirements

- Consume the A118 perception and workflow-state contracts.
- Return the A118 resolved-goal contract.
- Use only the A118 query and LLM ports for external calls.

## Validation commands

```bash
pytest -q tests/test_arc4_goal_resolver.py
```

## Assumptions and defaults

- Heuristic goal detection can reuse existing local ARC utilities where they remain isolated from the v1 orchestrator.
- Query-port calls must tolerate a fake or missing implementation in tests.