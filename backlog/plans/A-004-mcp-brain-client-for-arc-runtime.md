# A-004 - MCPBrainClient for ARC Runtime

## Card metadata

- Card: A004
- Priority: P0
- Depends on: A003

## Summary

Implement a production `MCPBrainClient` that preserves the runtime-facing brain client API while executing real SideQuests MCP tool calls underneath.

## Implementation approach

- add `MCPBrainClient` backed by the stdio MCP session manager
- map ARC runtime methods to SideQuests tool names
- keep `NoOpBrainClient` for baseline mode and test fakes
- update production augmented-mode wiring to use the MCP-backed client

## Concrete file additions/edits

- add or update ARC-side brain client module
- update runtime integration points that currently choose production brain clients
- remove direct production-side tool-handler loading from the augmented path

## API/interface changes

- `MCPBrainClient` must provide wrappers for:
  - `notify_turn`
  - `current_truth`
  - `register_plan`
  - `report_outcome`
  - `recall_plans`
  - `recall_relevant_lessons`
  - `analogical_search`
  - `upsert_lesson`
  - `recall_procedures`
  - `get_knowledge_gaps`
  - `branch_quest`
  - task-graph helpers as currently needed by runtime paths

## Tests to add or run

- wrapped method tests for at least:
  - `notify_turn`
  - `current_truth`
  - `register_plan`
  - `report_outcome`
  - `recall_plans`
  - `analogical_search`
- one integration-style test using a fake MCP session manager

## Validation commands

- `pytest -q tests/<new mcp brain client test file>`
- `rg -n "mcp_engine.tools|load_tool_handlers|LocalBrainClient" agents benchmarks`

## Assumptions/defaults

- method-style compatibility is preserved for the solver/orchestrator/runtime code
- response normalization belongs in the client seam, not in solver logic
