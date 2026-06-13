# Plan: A142 — Test Suite Hygiene: Fix Collection Errors, Expand Green Baseline

## Context

Current state (verified 2026-06-11):

- `pytest tests/ -q --co` → 1204 tests, **5 collection errors**, all `ModuleNotFoundError: No module named 'mcp_engine.config'` (or sibling submodules). The import chains run through production modules, e.g. `tests/test_model_constraints.py` → `benchmarks/arc3/model_eval.py:26` → `from mcp_engine.config import load_config`.
- `pytest` minus those 5 files → **6 failures**: 4 in `tests/test_arc3_durable_runner.py` (`test_loop_worker_survives_error`, `test_prompt_budget_comparison_report_distinguishes_first_input_shapes`, `test_meta_harness_runner_evaluates_candidate`, `test_upsert_lesson_round_trip` — failure text starts `Mod...`, i.e. ModuleNotFoundError at runtime), 1 in `tests/test_submission_compliance.py::test_submission_runner_initialization`, plus 1 more in the same area.
- A vestigial `mcp_engine/` directory sits at repo root; per CLAUDE.md the MCP seam forbids runtime code importing `mcp_engine.*` anyway — these are all benchmarks/offline or legacy paths.
- Green baseline `make test-a` = 18 tests (4 files). Full suite runs in <5s.

## Implementation Steps

### Step 1: Inventory the mcp_engine dependency surface

```bash
grep -rn "import mcp_engine\|from mcp_engine" --include="*.py" . | grep -v __pycache__ | grep -v .claude/worktrees
ls mcp_engine/
```

For each importing **production** module (e.g. `benchmarks/arc3/model_eval.py`), determine: is it reachable from `run_single_puzzle.py`, `make smoke`, or `benchmarks` scoring entrypoints? Use `grep -rn "model_eval\|ModelEvaluator"` etc.

### Step 2: Per-file disposition

For each of the 5 broken test files + the modules they test, choose exactly one:

a. **Port**: if the production module is alive, replace the `mcp_engine` import with the current equivalent (config loading likely moved — check `run_single_puzzle.py`'s `load_config` import for the live config loader; grep `def load_config` to find it).
b. **Delete**: if the production module is dead legacy (unreferenced outside its own tests), delete module + test, record the deletion in this plan's outcome notes and the tracker row.
c. **Skip-with-marker**: only if the feature is alive but needs unavailable infra; mark `@pytest.mark.requires_mcp`.

Do the same triage for the 6 runtime failures in `test_arc3_durable_runner.py` / `test_submission_compliance.py` — read each failure, classify as (a)/(b)/(c).

Decision rule for deletions: the module is dead if (1) no non-test imports, (2) no Makefile/script references, (3) not named in ARCHITECTURE.md as current. When in doubt, port rather than delete.

### Step 3: Markers and conftest

**File:** `pyproject.toml` (add `[tool.pytest.ini_options] markers = [...]`) and `tests/conftest.py`:

```python
import os
import pytest

def pytest_collection_modifyitems(config, items):
    skip_mcp = pytest.mark.skip(reason="requires CAMPY_MCP_CMD (live MCP daemon)")
    skip_api = pytest.mark.skip(reason="requires ARC_API_KEY (live ARC API)")
    for item in items:
        if "requires_mcp" in item.keywords and not os.environ.get("CAMPY_MCP_CMD"):
            item.add_marker(skip_mcp)
        if "requires_arc_api" in item.keywords and not os.environ.get("ARC_API_KEY"):
            item.add_marker(skip_api)
```

(Check whether `tests/conftest.py` already exists; merge if so.)

### Step 4: Makefile + docs

**File:** `Makefile` — add:

```make
test-all:
	$(PYTHON) -m pytest tests/ -q
```

**File:** `CLAUDE.md` — update non-negotiable #3: green-baseline signal is `make test-all`; `make test-a` remains the quick pre-commit subset. **File:** `ARCHITECTURE.md` — if A029 full-suite triage is mentioned, note it's resolved by A142.

### Step 5: The vestigial `mcp_engine/` directory

After Step 2, if nothing imports it anymore: delete the directory. If something legitimately still needs it (seam-exempt benchmarks code), leave it and document why in ARCHITECTURE.md. Run `tests/test_import_boundary.py` after either outcome.

### Step 6: Verify

```bash
.venv/bin/python -m pytest tests/ -q --co 2>&1 | tail -2   # 0 errors
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -3        # 0 failures
make test-a && make test-all
```

## Files Modified

| File | Change |
|------|--------|
| 5 broken test files + their production modules | port / delete / mark per disposition |
| `tests/test_arc3_durable_runner.py`, `tests/test_submission_compliance.py` | fix or mark 6 failures |
| `tests/conftest.py`, `pyproject.toml` | markers + env-gated skips |
| `Makefile`, `CLAUDE.md`, `ARCHITECTURE.md` | test-all baseline |
| possibly `mcp_engine/` | delete if orphaned |

## Conflict Note (for fan-out)

File-independent from A137-A141 (touches tests/config, not arc4 runtime). Safe to run fully in parallel. Note: A141 will modify test expectations in `harness`-adjacent tests — if both run concurrently, A142 should not "fix" failures introduced by A141's in-flight branch; triage against master.

## Risks

- Deleting a module that an undocumented script uses → mitigate with the three-point decision rule and grep sweep including `Makefile`, `docs/`, `benchmarks/`.
- `test_submission_compliance.py` may encode Kaggle submission requirements — prefer port/fix over delete there.
