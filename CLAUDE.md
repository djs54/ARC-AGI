# Claude Code Instructions — ARC_AGI

This repo is the ARC solver/evaluation workspace. The sibling `sidequests-brain` repo owns the memory engine; this repo is a pure MCP-over-stdio consumer.

## Canonical docs (keep these as the source of truth)

- [ARCHITECTURE.md](ARCHITECTURE.md) — system design, MCP seam contract, cognitive model, A-series notes. **Update this file first** when architectural facts change; other agent-facing docs (CLAUDE.md, AGENTS.md, GEMINI.md) intentionally stay thin pointers to it.
- [README.md](README.md) — setup, local dev, smoke invocation, MCP adapter wiring.
- [backlog/BacklogRules.md](backlog/BacklogRules.md) — backlog conventions. Active cards use the `A###` numeric ID with matching `backlog/Axxx.md` card + `backlog/plans/A-xxx-*.md` plan + row in `backlog/masterBacklogTracker.md`.

## Non-negotiables

1. **MCP seam only (runtime scope).** Runtime production code under `agents/`, `arc_runtime/`, `run_single_puzzle.py`, and `sidequest_mcp_client/` must not import `mcp_engine.*` or `sidequests.*`. `benchmarks/arc3/` is offline scoring / submission packaging and is exempt (A030) — it embeds the brain directly. `tests/test_import_boundary.py` enforces the runtime scope. The MCP stdio adapter lives in `sidequests-brain/sidequests/adapters/mcp_server.py` and is spawned via `SIDEQUESTS_MCP_CMD` — do not vendor it here.
2. **No ephemeral chips for follow-up work.** If you notice something worth doing later, write a `backlog/Axxx.md` + `backlog/plans/A-xxx-*.md` pair and add the tracker row. Never use `spawn_task` or other ephemeral mechanisms for persistent backlog work.
3. **Green-baseline signal is `make test-a`**, not `pytest -q`. The full-suite triage is tracked under A029 — do not restore full-suite green by re-introducing brain-internal imports.

## Development workflow

```bash
pip install -e ../sidequests-brain
pip install -e .
make test-a            # A-series green baseline (18/18)
make smoke             # live one-puzzle smoke (requires Ollama + brain daemon)
```

`SIDEQUESTS_MCP_CMD` must point at the sibling adapter — see [README.md](README.md) for the exact invocation.

## When architectural facts change

Edit [ARCHITECTURE.md](ARCHITECTURE.md). Do not duplicate its contents into this file.
