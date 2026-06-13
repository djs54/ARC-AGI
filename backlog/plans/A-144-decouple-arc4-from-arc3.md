# Plan: A144 — Decouple arc4 From arc3 Internals

## Context

Dependency inventory (verified 2026-06-11):

```
agents/arc4/evaluator.py:8    from agents.arc3.failure_taxonomy import FailureTaxonomy
agents/arc4/evaluator.py:199  from agents.arc3.failure_taxonomy import classify_failure   (inside classify_v2_termination)
run_single_puzzle.py:25       from agents.arc3.trace_names import (...)
run_single_puzzle.py:31       from agents.arc3.runner import DurableARCRunner
benchmarks/arc3/adapter.py    from agents.arc3.hypothesis import StateNode   (inside normalize_observation, ~line 1126; used only for StateNode.hash_grid)
```

`agents/arc3/failure_taxonomy.py` is small (enum + classifier). `agents/arc3/trace_names.py` is a new small module (untracked, added recently). `StateNode.hash_grid` — read `agents/arc3/hypothesis.py` to confirm, but it is a deterministic grid→sha256 hash; the moved copy must produce **identical** strings (artifacts and the graph store these hashes).

Note: if A143 has landed, the `run_single_puzzle.py` imports live in `arc_runtime/` modules instead — same moves apply, adjust paths.

## Implementation Steps

### Step 1: Create `agents/common/`

New package `agents/common/__init__.py`. It must stay dependency-light (stdlib only) so both arc3 and arc4 can import it without cycles.

### Step 2: Move failure taxonomy

- Copy `agents/arc3/failure_taxonomy.py` → `agents/common/failure_taxonomy.py` (verbatim).
- `agents/arc3/failure_taxonomy.py` becomes a shim: `from agents.common.failure_taxonomy import *  # noqa` plus explicit re-exports of `FailureTaxonomy`, `classify_failure` (star-import alone misses non-`__all__` names — re-export explicitly).
- Update `agents/arc4/evaluator.py` lines 8 and 199 to import from `agents.common.failure_taxonomy`.
- Grep for other arc3-internal importers of failure_taxonomy (e.g. `benchmarks/ab_harness.py`, telemetry) — leave them on the shim; only arc4 must move.

### Step 3: Replace StateNode.hash_grid in the adapter

Read `StateNode.hash_grid` in `agents/arc3/hypothesis.py`. Create `agents/common/grid_hash.py` with a function `hash_grid(grid)` whose implementation is copied **exactly** (same json serialization flags, same hash algo, same handling of empty/None). In `benchmarks/arc3/adapter.py` `normalize_observation`, replace the mid-function import with `from agents.common.grid_hash import hash_grid` (top of file) and `frame_hash = hash_grid(grid)`.

Equivalence test (in `tests/test_a144_decoupling.py`):

```python
def test_hash_grid_matches_statenode():
    from agents.arc3.hypothesis import StateNode
    from agents.common.grid_hash import hash_grid
    for grid in ([], [[0]], [[1,2],[3,4]], [[15]*64]*64):
        assert hash_grid(grid) == StateNode.hash_grid(grid)
```

(This test deliberately imports arc3 — it lives in tests, not runtime.) Optionally have `StateNode.hash_grid` delegate to the common function so there is one implementation.

### Step 4: Move trace_names

Copy `agents/arc3/trace_names.py` → `agents/common/trace_names.py`; shim the original; update the `run_single_puzzle.py` (or `arc_runtime/`) import.

### Step 5: Audit DurableARCRunner usage

```bash
grep -n "DurableARCRunner\|runner\." run_single_puzzle.py | head -40
```

Identify which attributes/methods the v2 path actually uses (expected: `harness`, `real_api`, config access, manifest tasks, `append_live_snapshot`, `world_model_eval`). Two acceptable outcomes — pick based on findings:

a. **Extract**: if v2 uses only harness+manifest+config, construct those directly (e.g. `ARC3Harness`, `load_tasks_from_manifest` are already imported from `benchmarks.arc3.harness`) and drop the `DurableARCRunner` import from the v2 path. v1 path keeps it.
b. **Document**: if v2 genuinely shares runner lifecycle (MCP readiness, artifact plumbing), keep the import but record in ARCHITECTURE.md that `agents.arc3.runner` is shared infrastructure pending extraction, and exclude it from the new boundary check (Step 6) with an explicit allowlist entry + comment.

Do not gold-plate: option (b) is fine for this card if extraction balloons.

### Step 6: Extend the import boundary test

**File:** `tests/test_import_boundary.py` — read its existing mechanism (AST or grep based) and add a rule: files under `agents/arc4/` must not import `agents.arc3.*`. Include the allowlist hook if Step 5 chose (b) (the allowlist applies to `run_single_puzzle.py`, not arc4 — arc4 must be clean unconditionally).

### Step 7: Hot-path proof + verify

```bash
# zero arc3 imports in arc4
grep -rn "agents.arc3" agents/arc4/ | grep -v __pycache__   # expect empty

# hot-path independence experiment (do NOT commit):
mv agents/arc3/orchestrator.py /tmp/ && PYTHONPATH=. .venv/bin/python run_single_puzzle.py --agent-version=v2 --num-puzzles 1 --max-steps 5; mv /tmp/orchestrator.py agents/arc3/

# v1 still works via shims:
PYTHONPATH=. .venv/bin/python run_single_puzzle.py --agent-version=v1 --num-puzzles 1 --max-steps 5

make test-a
.venv/bin/python -m pytest tests/test_a144_decoupling.py tests/test_import_boundary.py -q
```

## Files Modified

| File | Change |
|------|--------|
| `agents/common/{__init__,failure_taxonomy,grid_hash,trace_names}.py` | New shared package |
| `agents/arc3/{failure_taxonomy,trace_names}.py` | Re-export shims |
| `agents/arc3/hypothesis.py` | (optional) delegate hash_grid |
| `agents/arc4/evaluator.py` | Import from common |
| `benchmarks/arc3/adapter.py` | hash_grid from common; drop mid-function import |
| `run_single_puzzle.py` / `arc_runtime/` | trace_names from common; DurableARCRunner per Step 5 |
| `tests/test_import_boundary.py` | arc4→arc3 ban |
| `tests/test_a144_decoupling.py` | New — hash equivalence + boundary |

## Conflict Note (for fan-out)

Touches `evaluator.py` (conflicts with A137/A138 — land after them) and `run_single_puzzle.py`/`arc_runtime` (land after A143, or adjust paths). The adapter change is independent.

## Risks

- **Hash drift** silently corrupts grid-hash continuity in the graph and artifacts — the equivalence test is mandatory, and prefer delegation over duplication.
- Circular imports if `agents/common` grows dependencies — keep it stdlib-only, enforce by eyeball in review.
