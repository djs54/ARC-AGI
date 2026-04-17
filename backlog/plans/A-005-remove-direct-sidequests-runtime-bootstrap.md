# A-005 - Remove Direct SideQuests Runtime Bootstrap

## Card metadata

- Card: A005
- Priority: P0
- Depends on: A003, A004

## Summary

Remove direct SideQuests bootstrap from production ARC run paths and replace it with MCP readiness checks.

## Implementation approach

- update production entrypoints so they no longer import SideQuests config/schema/Kuzu/loop bootstrap modules directly
- replace local bootstrap with an MCP readiness path:
  - start or attach to MCP session
  - initialize
  - list tools
  - verify required tool set
- fail fast with one clear error message if SideQuests is unavailable

## Concrete file additions/edits

- update production entrypoints such as submission and single-runner paths
- add readiness-check helper(s)
- add import-boundary verification support in tests or scripts

## API/interface changes

- production startup contract changes from “bootstrap SideQuests locally” to “connect to SideQuests through MCP and verify readiness”

## Tests to add or run

- readiness success
- readiness failure with clear message
- import-boundary test or grep-based check for production files
- integration smoke test against a real or controlled MCP server

## Validation commands

- `pytest -q tests/<new readiness test file>`
- `rg -n "from mcp_engine|import mcp_engine|from sidequests|import sidequests" agents benchmarks run_single_puzzle.py`

## Assumptions/defaults

- production ARC augmented mode requires a SideQuests service boundary to be available
- direct bootstrap removal applies to production paths first; test fixtures may still use controlled fakes where needed
