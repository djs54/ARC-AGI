# ARC_AGI

`ARC_AGI/` is the repo-shaped extraction workspace for the ARC effort.

The goal is to keep ARC work separate from the core HippoCampy/Campy memory product:

- `hippocampy` remains the local memory engine
- `ARC_AGI` becomes the ARC solver/evaluation project
- `ARC_AGI` consumes `hippocampy` as a dependency

For the canonical system design of this repo, see [ARCHITECTURE.md](ARCHITECTURE.md).

## What Is In Here

- `agents/arc4/`
  ARC v2 modular workflow — the only supported agent (v1 was retired to `archive/agents-arc3/`, see A148)
- `benchmarks/arc3/`
  ARC harness, packaging, and compliance tooling
- `sidequest_mcp_client/`
  ARC-owned MCP client seam for runtime access to the sibling `hippocampy` memory server
- `tests/`
  ARC-specific test set copied from the main repo
- `run_single_puzzle.py`
  Single-puzzle runner (`--agent-version=v2`, currently the only supported value)
- `archive/agents-arc3/`
  Retired v1 agent, its test suite, and the v1-vs-v2 comparison harness — kept for reference only, not part of the runtime

## Dependency Model

This workspace is intentionally not a memory engine by itself.

Runtime ARC paths rely on HippoCampy for:

- the MCP stdio server adapter
- graph-backed memory tools
- persistent storage and retrieval
- shared config and observability support exposed through the sibling repo

Offline benchmark and submission tooling under `benchmarks/arc3/` is exempt from the runtime seam rule and still embeds brain internals directly where packaging constraints require it.

That means the intended relationship is:

1. `hippocampy` provides local memory and retrieval
2. `ARC_AGI` imports and uses it

### MCP v1 — stdio-only production seam

For production (v1) the canonical seam between `ARC_AGI` and HippoCampy is MCP over stdio. Production code now uses only the MCP-facing client modules in `sidequest_mcp_client/` (`mcp_brain_client`, `mcp_session`, `readiness`, `observability`). Any direct-import compatibility helpers are isolated under `sidequest_mcp_client/test_compat/` for tests only. Production code should not rely on direct imports of `mcp_engine.*` or other HippoCampy internals.

The MCP stdio adapter that serves this seam lives in the sibling `hippocampy` repo at `campy/adapters/mcp_server.py`. `ARC_AGI` is a pure consumer — it spawns the adapter as a subprocess at runtime via the `CAMPY_MCP_CMD` environment variable. Do not vendor the adapter into this repo: it imports brain internals (unix-socket path, offline-queue format) and is shared with other MCP clients (Smithery, Claude Desktop, Cursor).

## Local Development

From inside `ARC_AGI/`, the intended setup is:

```bash
pip install -e ../hippocampy
pip install -e .
make test-a
```

`make test-a` runs the A-series observability, plan-registration, exploration-probing, and trace-durability test files and remains the required green-baseline signal for active A-card work. The broader `pytest -q` baseline was restored through the A029 follow-up sequence and is recorded on A037 as 723/723 passing.

If `hippocampy` is published where you want to consume it from, you can install that package instead of using the parent path.

### Running a smoke

Point `CAMPY_MCP_CMD` at the sibling repo's adapter, then run the live smoke. v2 (`agents/arc4/`) is
the only supported agent — v1 was retired in A148.

```bash
export CAMPY_MCP_CMD="../hippocampy/.venv/bin/python -m campy.adapters.mcp_server"
PYTHONPATH=. .venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 10
```

`--live-smoke` implies `--real-api`, auto-loads `ARC_API_KEY` from `benchmarks/.arc/arc.json`, and uses forgiving local-Ollama timeouts. The brain daemon must be running (socket at `~/.campy/brain.sock`).

Current rollout caveat: ARC v2 is wired to ARC-specific MCP query tools from the sibling `hippocampy` repo. If the running MCP server does not yet expose those `arc_*` methods, the ARC v2 runtime degrades gracefully instead of crashing, but smoke quality is limited until the server side is updated.

## Current Status

This is a clean separation scaffold, not a full migration completion.

What is already done:

- ARC code is copied into its own repo-shaped folder
- ARC tests are copied into their own test tree
- packaging metadata for a standalone ARC project is added
- Production HippoCampy integration is concentrated behind MCP-facing modules in `sidequest_mcp_client/`, with any direct-import compatibility helpers isolated to `sidequest_mcp_client/test_compat/`
- A modular ARC v2 runtime prototype now exists under `agents/arc4/` and can be selected with `run_single_puzzle.py --agent-version=v2`
- ARC v2 integration tests cover the workflow slice, phase modules, MCP adapter mapping, CLI flag support, and telemetry emission

What still remains if you want a fully independent git repo:

- initialize a separate git repo inside `ARC_AGI/`
- move ARC-only docs and result artifacts over time

Current validation baseline:

- `make test-a` is the required green-baseline signal for A-card work
- focused ARC v2 regression coverage lives under `tests/test_arc4_*.py` and `tests/test_arc4_integration.py`
- full-suite triage and restoration history remains tracked through A029 and its follow-up sequence

The decision on `mcp_engine` direct imports has already landed: A002/A005/A006 moved all production paths behind the MCP stdio seam, and `BacklogRules.md` rule 4 forbids re-introducing direct imports. See [ARCHITECTURE.md](ARCHITECTURE.md) for the seam contract.

## Recommendation

Keep `ARC_AGI` as the ARC lab and benchmark project.
Keep `hippocampy` as the memory product.

That separation matches the product direction much better than continuing to let both efforts share the same top-level codebase.
