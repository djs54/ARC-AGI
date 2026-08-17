# ARC_AGI

`ARC_AGI` builds a reasoning harness: scaffolding that lets cheaper, local-friendly LLMs work through difficult puzzles and problems they can't solve well unprompted. Instead of asking one model call to do everything, the harness wraps a model in a structured solve loop — perceive, resolve a goal, plan, vet the plan, execute, evaluate — backed by a persistent, graph-shaped world model that tracks what's been tried, what's been falsified, and what transfers, so the model doesn't have to re-derive it every step.

ARC-AGI-3 is the current benchmark and proving ground for that harness, not the end goal. The reasoning loop and the world-model machinery are the actual product; the puzzle suite is the yardstick that keeps it honest.

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

This workspace is intentionally not a memory engine by itself. HippoCampy is a supporting dependency the harness relies on for memory.

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
