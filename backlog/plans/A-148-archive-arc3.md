# Plan: A148 — Isolate and Archive agents/arc3 (v1 Agent)

## Executed 2026-08-02 — deviations from plan

Branch A was executed; see `backlog/A148.md`'s Resolution section for the full account. Two things
this plan didn't anticipate, sized correctly here for future reference:

1. **Test file count.** Step A3 said "update `tests/` that import `agents.arc3.*`" without sizing it.
   The actual count was 128 files (the full pre-A130 v1-era test suite), moved as a block into
   `archive/agents-arc3/tests/` rather than individually skip-marked — mechanical `git mv`, no
   per-file editing, since none of them are consumed by anything outside the archived set (verified:
   no cross-imports either direction between the moved files and what stayed in `tests/`).
2. **`benchmarks/arc3/submission.py` coupling.** Step 2's grep (`grep -rn "agents\.arc3\|..." arc_runtime run_single_puzzle.py`) only checked `arc_runtime`/`run_single_puzzle.py`, not `benchmarks/`.
   `submission.py` turned out to import `DurableARCRunner` directly and is otherwise unreferenced by
   any Makefile target — moved into the archive alongside the source rather than left as a dangling
   import in a directory the card says must stay untouched. Confirmed (grep) it's the *only* file
   under `benchmarks/arc3/` with any `agents.arc3` coupling.

## Context

Post-A144 the runtime → `agents.arc3` surface is a single symbol:

```bash
grep -rn "agents\.arc3\|from agents import arc3" --include="*.py" arc_runtime run_single_puzzle.py | grep -v __pycache__
# arc_runtime/runner_shell.py:32  from agents.arc3.runner import DurableARCRunner
# arc_runtime/runner_shell.py:340 (lazy re-import)
# run_single_puzzle.py:29 / :265  from agents.arc3.runner import DurableARCRunner
```

`DurableARCRunner` is the `--agent-version=v1` engine. `agents/arc3/` is ~30K LOC; the bulk (`orchestrator.py` 9,602, `solver.py` 4,492) is reachable only through it. `benchmarks/arc3/**` is separate, seam-exempt, and actively used by arc4 — explicitly out of scope.

## Step 0: Decide v1's fate (gate)

Answer, with the user, before touching code:

1. Is the v1↔v2 comparison (A125 `make smoke-compare`) still decision-useful, or has v2 been the sole production agent long enough to drop it?
2. Is a runnable v1 needed for regression, or would a frozen git tag suffice?

Outcome selects branch **(A) retire** or **(B) freeze**. Record the decision in ARCHITECTURE.md regardless.

## Branch A — retire v1

### A1. Remove the runtime dependency
- Delete the `--agent-version=v1` branch in `run_single_puzzle.py` (the `DurableARCRunner` construction at ~L265) and in `arc_runtime/runner_shell.py` (~L335-342). Keep v2 as the only path; simplify the arg parser (or keep `--agent-version` accepting only `v2` for back-compat).
- Grep the comparison harness (`benchmarks/`, `make smoke-compare`) for v1 construction and retire or repoint it.

### A2. Confirm isolation
```bash
grep -rn "agents\.arc3" --include="*.py" arc_runtime run_single_puzzle.py benchmarks | grep -v __pycache__
# expect: only benchmarks/arc3 internal references; nothing importing agents.arc3.*
```

### A3. Move the tree
- `git mv agents/arc3 archive/agents-arc3` (or `agents/_archive/arc3`). Update `agents/common` shims if any re-export from arc3 (A144 created shims in `agents/arc3/{failure_taxonomy,trace_names}.py` — move the canonical copies to `agents/common` fully and drop the arc3 shims, or keep the shims at the archive path).
- Update `tests/` that import `agents.arc3.*`: either move v1 tests alongside the archive or mark them `@pytest.mark.skip(reason="v1 archived (A148)")`.

### A4. Boundary rule
- Extend `tests/test_import_boundary.py`: nothing under `arc_runtime/`, `run_single_puzzle.py`, `agents/arc4/` may import from the archive path.

## Branch B — freeze v1 in place

### B1. Cap the surface
- Extend `tests/test_import_boundary.py` with a rule: runtime (`arc_runtime/`, `run_single_puzzle.py`) may import **only** `agents.arc3.runner.DurableARCRunner` from `agents.arc3` — any deeper or additional `agents.arc3.*` import fails the test. This prevents new coupling without moving code.

### B2. Document frozen status
- ARCHITECTURE.md: mark `agents/arc3/` frozen/maintenance-only; new agent work goes to arc4. Note the single sanctioned import.

### B3. Stop uncarded arc3 feature work
- The route-backed-progress refinements (committed 2026-06-13) are the kind of change that should stop under a freeze. Note in ARCHITECTURE.md that arc3 changes now require an explicit card justifying why v1 (not v2) is the right place.

## Verify (either branch)

```bash
make test-all
PYTHONPATH=. .venv/bin/python run_single_puzzle.py --agent-version=v2 --num-puzzles 1 --max-steps 5  # v2 unaffected
# Branch A only: confirm v1 path is gone / errors cleanly
# Branch B only: confirm the boundary test rejects a deeper agents.arc3 import
```

## Files (branch-dependent)

| File | Branch A | Branch B |
|------|----------|----------|
| `run_single_puzzle.py`, `arc_runtime/runner_shell.py` | drop v1 path | unchanged |
| `agents/arc3/**` | `git mv` to archive | unchanged (frozen) |
| `tests/test_import_boundary.py` | ban archive imports in runtime | restrict to `runner.DurableARCRunner` |
| `ARCHITECTURE.md` | record retirement | record freeze |
| comparison harness | retire/repoint | unchanged |

## Risks

- **Hidden v1 consumers.** Grep `benchmarks/` and any scripts/Makefile targets for v1 before moving. `make smoke-compare` is the known one.
- **A144 shims live in arc3.** `agents/arc3/{failure_taxonomy,trace_names}.py` re-export from `agents/common`; if arc3 moves, ensure no one imports those shim paths (grep first).
- Branch A is higher-risk/higher-reward; Branch B is the safe default if the comparison is still wanted.
