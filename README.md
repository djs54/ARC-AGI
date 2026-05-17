# ARC_AGI

`ARC_AGI/` is the repo-shaped extraction workspace for the ARC effort.

The goal is to keep ARC work separate from the core HippoCampy/Campy memory product:

- `hippocampy` remains the local memory engine
- `ARC_AGI` becomes the ARC solver/evaluation project
- `ARC_AGI` consumes `hippocampy` as a dependency

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

It still relies on HippoCampy for:

- `mcp_engine`
- graph schema and Kuzu setup
- memory tools
- observability helpers
- shared config loading

That means the intended relationship is:

1. `hippocampy` provides local memory and retrieval
2. `ARC_AGI` imports and uses it

### MCP v1 — stdio-only production seam

For production (v1) the canonical seam between `ARC_AGI` and HippoCampy is MCP over stdio. Production code now uses only the MCP-facing client modules in `sidequest_mcp_client/` (`mcp_brain_client`, `mcp_session`, `readiness`, `observability`). Any direct-import compatibility helpers are isolated under `sidequest_mcp_client/test_compat/` for tests only. Production code should not rely on direct imports of `mcp_engine.*` or other HippoCampy internals.

The MCP stdio adapter that serves this seam lives in the sibling `hippocampy` repo at `campy/adapters/mcp_server.py`. `ARC_AGI` is a pure consumer — it spawns the adapter as a subprocess at runtime via the `CAMPY_MCP_CMD` environment variable. Do not vendor the adapter into this repo: it imports brain internals (unix-socket path, offline-queue format) and is shared with other MCP clients (Smithery, Claude Desktop, Cursor).

## Local Development

From inside `ARC_AGI/`, the intended setup is:

```bash
pip install -e ../sidequests-brain  # HippoCampy/Campy repo; use ../hippocampy if your checkout was renamed
pip install -e .
make test-a
```

`make test-a` runs the A-series observability, plan-registration, exploration-probing, and trace-durability test files and remains the required green-baseline signal for active A-card work. The broader `pytest -q` baseline was restored through the A029 follow-up sequence and is recorded on A037 as 723/723 passing.

If `hippocampy` is published where you want to consume it from, you can install that package instead of using the parent path.

### Running a smoke

Point `CAMPY_MCP_CMD` at the sibling repo's adapter, then run the live smoke:

```bash
export CAMPY_MCP_CMD="../sidequests-brain/.venv/bin/python -m campy.adapters.mcp_server"
PYTHONPATH=. .venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 10
```

`--live-smoke` implies `--real-api`, auto-loads `ARC_API_KEY` from `benchmarks/.arc/arc.json`, and uses forgiving local-Ollama timeouts. The brain daemon must be running (socket at `~/.campy/brain.sock`).

## Current Status

This is a clean separation scaffold, not a full migration completion.

What is already done:

- ARC code is copied into its own repo-shaped folder
- ARC tests are copied into their own test tree
- packaging metadata for a standalone ARC project is added
- Production HippoCampy integration is concentrated behind MCP-facing modules in `sidequest_mcp_client/`, with any direct-import compatibility helpers isolated to `sidequest_mcp_client/test_compat/`

What still remains if you want a fully independent git repo:

- initialize a separate git repo inside `ARC_AGI/`
- move ARC-only docs and result artifacts over time

Current validation baseline:

- `make test-a` is the required green-baseline signal for A-card work
- `pytest -q` was restored to a full-suite green baseline through A029/A030-A037 (`723/723` passing as recorded on A037)

The decision on `mcp_engine` direct imports has already landed: A002/A005/A006 moved all production paths behind the MCP stdio seam, and `BacklogRules.md` rule 4 forbids re-introducing direct imports. See [ARCHITECTURE.md](ARCHITECTURE.md) for the seam contract.

## Recommendation

Keep `ARC_AGI` as the ARC lab and benchmark project.
Keep `hippocampy` as the memory product.

That separation matches the product direction much better than continuing to let both efforts share the same top-level codebase.
