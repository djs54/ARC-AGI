# ARC_AGI

`ARC_AGI/` is the repo-shaped extraction workspace for the ARC effort.

The goal is to keep ARC work separate from the core SideQuests/Campy memory product:

- `sidequests-brain` remains the local memory engine
- `ARC_AGI` becomes the ARC solver/evaluation project
- `ARC_AGI` consumes `sidequests-brain` as a dependency

For the canonical system design of this repo, see [ARCHITECTURE.md](ARCHITECTURE.md).

## What Is In Here

- `agents/arc3/`
  ARC orchestration and solver logic
- `benchmarks/arc3/`
  ARC harness, submission, packaging, and compliance tooling
- `tests/`
  ARC-specific test set copied from the main repo
- `run_single_puzzle.py`
  Single-puzzle runner for ARC smoke work

## Dependency Model

This workspace is intentionally not a memory engine by itself.

It still relies on SideQuests for:

- `mcp_engine`
- graph schema and Kuzu setup
- memory tools
- observability helpers
- shared config loading

That means the intended relationship is:

1. `sidequests-brain` provides local memory and retrieval
2. `ARC_AGI` imports and uses it

### MCP v1 — stdio-only production seam

For production (v1) the canonical seam between `ARC_AGI` and SideQuests is MCP over stdio. Development convenience wrappers that import SideQuests internals are transitional only. Developers should prefer a local `sidequests-brain` MCP process or a published client/SDK and interact via the MCP stdio contract described in `ARCHITECTURE.md`. Production code should not rely on direct imports of `mcp_engine.*` or other SideQuests internals.

## Local Development

From inside `ARC_AGI/`, the intended setup is:

```bash
pip install -e ../sidequests-brain
pip install -e .
pytest
```

If `sidequests-brain` is published where you want to consume it from, you can install that package instead of using the parent path.

## Current Status

This is a clean separation scaffold, not a full migration completion.

What is already done:

- ARC code is copied into its own repo-shaped folder
- ARC tests are copied into their own test tree
- packaging metadata for a standalone ARC project is added
- SideQuests runtime imports are concentrated behind `sidequests_bridge/` instead of being scattered across the ARC runtime

What still remains if you want a fully independent git repo:

- initialize a separate git repo inside `ARC_AGI/`
- trim or replace any imports that should no longer come from the parent repo
- decide whether ARC should keep importing `mcp_engine` directly or call SideQuests only through a narrower API/client package
- move ARC-only docs and result artifacts over time

## Recommendation

Keep `ARC_AGI` as the ARC lab and benchmark project.
Keep `sidequests-brain` as the memory product.

That separation matches the product direction much better than continuing to let both efforts share the same top-level codebase.
