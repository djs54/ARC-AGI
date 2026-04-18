# Extraction Status

## Purpose

This folder is the clean separation point between:

- the core SideQuests/Campy memory system
- the ARC-specific solver and benchmark effort

## Current Boundary

ARC-owned code now lives under:

- `ARC_AGI/agents/`
- `ARC_AGI/benchmarks/`
- `ARC_AGI/tests/`
- `ARC_AGI/run_single_puzzle.py`
- `ARC_AGI/ARCHITECTURE.md`
- `ARC_AGI/sidequest_mcp_client/`

Core-memory code still lives in the parent project and is consumed as a dependency:

- `mcp_engine/`
- `sidequests/`
- adapters and memory tooling

Current caveat:

- `ARC_AGI/sidequest_mcp_client/` now serves as the MCP-facing production seam for ARC runtime code.
- Direct-import compatibility helpers have been moved out of production paths and isolated under `ARC_AGI/sidequest_mcp_client/test_compat/` for tests only. The v1 production seam is MCP over stdio only and ARC runtime code interacts with SideQuests through the MCP client contract rather than direct imports.

Startup/readiness expectations:

- ARC components should wait for an MCP client `ready` handshake before issuing memory/tool calls.
- The ARC-side client should expose lifecycle methods (initialize_session, list_tools, call_tool, close) and emit readiness and error metrics so orchestration code can make safe startup and retry decisions.


## Why This Structure

This gives you a practical split immediately:

- SideQuests can move forward as the product
- ARC can continue as a separate experiment/research repo
- the ARC effort can still benefit from SideQuests without owning its internals

## Next Step If You Want A Full Repo Split

1. `cd ARC_AGI`
2. `git init`
3. commit the extracted ARC workspace
4. optionally replace direct `mcp_engine.*` imports with a smaller public client surface from `sidequests-brain`

## Important Note

This extraction is intentionally non-destructive.
The original ARC files remain in the main repo for now so nothing else breaks while you decide how aggressively to complete the split.
