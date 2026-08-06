# Archived: agents/arc3 (v1 agent)

Retired 2026-08-02 per [backlog/A148.md](../../backlog/A148.md) (branch A: retire). This directory holds the
former `agents/arc3/` package (~29K LOC, the pre-`agents/arc4` ARC agent), its dedicated test suite
(`tests/`, moved here as-is), the v1-only submission script (`submission.py`, formerly
`benchmarks/arc3/submission.py`), and the v1-vs-v2 comparison test
(`tests/test_arc4_v1_v2_comparison.py`).

## Why this was archived

`agents/arc4` (v2) has been the only actively developed ARC agent for a long stretch of the backlog
(A130 onward). After A144 decoupled v2 from v1 internals, the only remaining runtime dependency on
v1 was a single symbol, `DurableARCRunner`, wired behind `--agent-version=v1`. The v1-vs-v2 comparison
harness (`make smoke-compare`) that justified keeping v1 runnable had itself been failing throughout
recent work (v2 winning only 1/4 metrics against a stale, disconnected snapshot) and wasn't in active
use. Retiring removed a persistent test-suite failure, ~29K lines of unmaintained code, and a CLI
branch that existed only to serve a comparison nobody was watching.

## What this is NOT

This is not a guarantee the code still runs. Nothing here is imported by production runtime code
(`agents/arc4`, `arc_runtime/`, `run_single_puzzle.py`) — enforced by
`tests/test_import_boundary.py::test_runtime_has_no_archived_arc3_imports`. It is kept for git
history and manual reference only. `benchmarks/arc3/` (harness, adapter, world_model_eval, etc.)
is unrelated and stays active — arc4 depends on it directly.

## If you need to run v1 again

Everything here is intact and importable in isolation; it just isn't wired into the CLI anymore.
`git log --follow` on any file here will show its full history from before the move.
