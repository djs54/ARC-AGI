# Claude Code Instructions — ARC_AGI

Codex/Aider read [AGENTS.md](AGENTS.md); Gemini CLI reads [GEMINI.md](GEMINI.md). All three are thin pointers to the single source of truth below.

## Canonical docs (keep these as the source of truth)

- [ARCHITECTURE.md](ARCHITECTURE.md) — system design, MCP seam contract, cognitive model, A-series notes. **Update this file first** when architectural facts change.
- [README.md](README.md) — setup, local dev, smoke invocation, MCP adapter wiring.
- [backlog/BacklogRules.md](backlog/BacklogRules.md) — backlog conventions. Active cards use the `A###` numeric ID with matching `backlog/Axxx.md` card + `backlog/plans/A-xxx-*.md` plan + row in `backlog/masterBacklogTracker.md`.

## Current ARC-AGI-3 strategy

Before implementing architecture-affecting ARC runtime work, read [ARCHITECTURE.md](ARCHITECTURE.md), especially `ARC-AGI-3 Strategic Architecture` and `ARC v2 Runtime Prototype`. Then invoke the `arc-graph-engineering-review` skill (`.claude/skills/arc-graph-engineering-review/`) — it turns the Shift A/B/C principles and the Graph-Guided Investigation Loop into a concrete, checkable review, not just directional intent. Also invoke it before considering ARC runtime work complete, and when investigating a live-run bug or anomaly (your own investigation process is in scope, not just the code you ship).

Technical mission statement:

> GPT-5.5-style reasoning should generate hypotheses, but the graph world model should decide what is believed, what is falsified, what transfers, and what experiment is worth paying for next.

Relevant backlog sequences:

- strategic world-model direction: A073-A078
- current executable ARC v2 prototype: A118-A123, with B278 in the sibling `hippocampy` repo

## Non-negotiables

1. **MCP seam only (runtime scope).** Runtime production code under `agents/`, `arc_runtime/`, `run_single_puzzle.py`, and `sidequest_mcp_client/` must not import `mcp_engine.*` or `campy.*` / `sidequests.*`. `benchmarks/arc3/` is offline scoring / submission packaging and is exempt (A030) — it embeds the brain directly. `tests/test_import_boundary.py` enforces the runtime scope. The MCP stdio adapter lives in the sibling `hippocampy` repo at `campy/adapters/mcp_server.py` and is spawned via `CAMPY_MCP_CMD` — do not vendor it here.
2. **Persistent backlog for follow-up work.** New work goes into a `backlog/Axxx.md` + `backlog/plans/A-xxx-*.md` pair with a tracker row. No ephemeral task mechanisms.
3. **Green-baseline signal is `make test-all`**. Keep `make test-a` as the fast pre-commit subset.
4. **Branch + PR for every change, no direct commits to `master`.** `master` has branch protection (PR required, `test-a` required status check) — create a feature branch before the first commit of any new work, open a PR, and let real CI run before merging. This applies even to a one-line fix discovered mid-task; "just a quick continuation" is not an exception (see `backlog/A207.md`'s history for why this is written down).

## Development workflow

```bash
pip install -e ../hippocampy
pip install -e .
make test-a
make test-all
make smoke
```

## When architectural facts change

Edit [ARCHITECTURE.md](ARCHITECTURE.md). Do not duplicate its contents into this file.
