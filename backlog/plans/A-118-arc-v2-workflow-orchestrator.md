# Plan: A-118 — ARC v2 Workflow Contracts and Orchestrator

## Card metadata

- **Card:** A118
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** None
- **Intended executor:** GPT-5.4-mini subagent

## Summary

Create the ARC v2 package foundation that every later card codes against. This card is not only the state machine. It is the contract owner for shared ARC v2 types, workflow state, and injected ports.

## Why this card unlocks parallel work

After A118 lands, A119-A122 can be handed to separate cheap subagents without them needing to negotiate payload shapes. They all import the same contracts from `agents.arc4.types` and `agents.arc4.ports` and nothing else from sibling ARC v2 modules.

## Implementation approach

### Step 1: Create the package and contract files

Create:

- `agents/arc4/__init__.py`
- `agents/arc4/types.py`
- `agents/arc4/ports.py`
- `agents/arc4/workflow.py`

`types.py` should own the serializable dataclasses or typed structures needed across cards. At minimum define:

- workflow state
- phase result envelope
- perception snapshot
- goal hypothesis and resolved goal payload
- plan candidate and planning result payload
- vet decision payload
- execution result payload
- evaluation result payload

Keep the shapes small, explicit, and serializable. Do not let later cards invent new top-level payload contracts unless this plan is updated first.

`ports.py` should own the injected boundaries. Define protocol-style interfaces for:

- graph-query access used by A119-A123
- optional LLM access used by A120 and A121
- each workflow phase callable signature

### Step 2: Implement the thin workflow

`workflow.py` should own:

- the fixed phase order `PERCEIVE -> RESOLVE -> PLAN -> VET -> EXECUTE -> EVALUATE`
- one workflow dependency bundle that holds the injected phase callables
- budget guard
- one replan pass after a veto
- stall detection
- crash capture with full traceback in the packaged result

It must not own goal inference, candidate ranking, graph reads, or effect interpretation. Those belong to later cards.

### Step 3: Put cross-step ephemeral state in one place

The following counters or fields must live in `WorkflowState` because later cards need them and they should not be hidden inside module globals:

- step index and termination state
- consecutive no-progress count
- previous grid hash or loop history pointer
- active goal snapshot
- action attempt counts
- action falsification counts
- latest veto reason or alternative suggestion

### Step 4: Write focused tests

Add `tests/test_arc4_workflow.py` with focused unit coverage for:

- phase order
- budget guard
- crash guard
- single veto-triggered replan pass
- skip/terminate behavior after a second veto or terminal evaluation
- stall termination after repeated no-progress evaluations

## Concrete file edits

- `agents/arc4/__init__.py`
- `agents/arc4/types.py`
- `agents/arc4/ports.py`
- `agents/arc4/workflow.py`
- `tests/test_arc4_workflow.py`

## API and interface requirements

- `workflow.py` may import from `agents.arc4.types` and `agents.arc4.ports` only.
- A119-A122 must be able to import `types.py` and `ports.py` without importing `workflow.py` internals.
- The graph-query and LLM ports must be injectable test doubles, not concrete MCP or runtime clients.

## Validation commands

```bash
pytest -q tests/test_arc4_workflow.py
```

## Assumptions and defaults

- The ARC v2 package lives under `agents/arc4/` and is allowed under the repo import-boundary rules.
- Persistence of durable graph beliefs stays outside this card. `WorkflowState` is only per-run ephemeral state.
- If a later card needs a new shared contract field, update `types.py` here first rather than inventing a local dict shape.