# Gemini CLI Instructions — ARC_AGI

Claude Code reads [CLAUDE.md](CLAUDE.md); Codex/Aider read [AGENTS.md](AGENTS.md). All three are thin pointers to the single source of truth below.

## Canonical docs (keep these as the source of truth)

- [ARCHITECTURE.md](ARCHITECTURE.md) — system design, MCP seam contract, cognitive model, A-series notes. **Update this file first** when architectural facts change.
- [README.md](README.md) — setup, local dev, smoke invocation, MCP adapter wiring.
- [backlog/BacklogRules.md](backlog/BacklogRules.md) — backlog conventions. Active cards use the `A###` numeric ID with matching `backlog/Axxx.md` card + `backlog/plans/A-xxx-*.md` plan + row in `backlog/masterBacklogTracker.md`.

## Non-negotiables

1. **MCP seam only.** Production code under `agents/`, `arc_runtime/`, and `sidequest_mcp_client/` must not import `mcp_engine.*` or `sidequests.*` at runtime (BacklogRules rule 4; `tests/test_import_boundary.py` enforces). The MCP stdio adapter lives in `sidequests-brain/sidequests/adapters/mcp_server.py` and is spawned via `SIDEQUESTS_MCP_CMD` — do not vendor it here.
2. **Persistent backlog for follow-up work.** New work goes into a `backlog/Axxx.md` + `backlog/plans/A-xxx-*.md` pair with a tracker row. No ephemeral task mechanisms.
3. **Green-baseline signal is `make test-a`**, not `pytest -q`. Full-suite triage is tracked under A029.

## Development workflow

```bash
pip install -e ../sidequests-brain
pip install -e .
make test-a
make smoke
```

## When architectural facts change

Edit [ARCHITECTURE.md](ARCHITECTURE.md). Do not duplicate its contents into this file.
