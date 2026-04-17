# A-003 - STDIO MCP Session Manager

## Card metadata

- Card: A003
- Priority: P0
- Depends on: A002

## Summary

Implement the low-level stdio MCP client/session manager used by all production ARC-to-SideQuests communication.

## Implementation approach

- add one transport module responsible for launching the generic SideQuests MCP stdio server command
- implement initialize, `tools/list`, and `tools/call`
- normalize MCP envelopes and textual content payloads in one place
- provide structured error types for startup failure, malformed response, missing tool, and timeout

## Concrete file additions/edits

- add a new transport/client module under the current bridge area or a renamed client package
- add targeted tests with a fake MCP stdio server fixture
- update architecture/setup docs if module names change

## API/interface changes

- add reusable client/session methods:
  - `start()`
  - `initialize()`
  - `list_tools()`
  - `call_tool(name, arguments)`
  - `close()`

## Tests to add or run

- initialize success
- tools/list success
- tool call success
- startup failure
- malformed JSON/MCP response
- missing tool
- clean shutdown

## Validation commands

- `pytest -q tests/<new mcp session manager test file>`
- `rg -n "stdio|tools/list|tools/call|initialize" <new module> tests`

## Assumptions/defaults

- the command target is the generic SideQuests MCP stdio server, not a direct brain daemon bootstrap path
- response normalization happens at the transport layer, not in each caller
