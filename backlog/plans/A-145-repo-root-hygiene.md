# Plan: A145 — Repo Root Hygiene

## Context

Root listing as of 2026-06-11 contains, beyond legitimate files: 7 `A092-A095_*.md` scratch docs, `EXTRACTION_STATUS.md`, 4 `submission_results*` JSON/JSONL artifacts, `agent_execution_trace.json`, `master_timeline.json`, `test_phoenix.py`, two `*.egg-info/` dirs, root `__pycache__/`, `backlog/.!48529!masterBacklogTracker.md`, and dual configs `campy.toml`/`sidequests.toml`.

The artifact paths are load-bearing: `run_single_puzzle.py` writes them (search `submission_results_single`, `agent_execution_trace`, `master_timeline`), the run_review block embeds `file://` URLs to them, `benchmarks/arc3/world_model_eval.py` reads the world-model live file, the Makefile smoke targets may reference them, and tests assert on them (`tests/test_a088_compact_smoke_artifact_exports.py` and others — grep).

## Implementation Steps

### Step 1 Inventory Disposition (2026-06-13)

| Item | References Found | Disposition |
|------|------------------|-------------|
| `A092-A095_CODEBASE_SURVEY.md` | referenced by sibling A092 docs + `docs/a-series-wip-audit.md` + this plan | Move to `docs/archive/` |
| `A092-A095_CODE_PATTERNS.md` | referenced by sibling A092 docs + `docs/a-series-wip-audit.md` + this plan | Move to `docs/archive/` |
| `A092-A095_EXPLORATION_COMPLETE.md` | referenced by sibling A092 docs + `docs/a-series-wip-audit.md` + this plan | Move to `docs/archive/` |
| `A092-A095_EXPLORATION_INDEX.md` | referenced by sibling A092 docs + `docs/a-series-wip-audit.md` + this plan | Move to `docs/archive/` |
| `A092-A095_EXPLORATION_SUMMARY.md` | referenced by sibling A092 docs + `docs/a-series-wip-audit.md` + this plan | Move to `docs/archive/` |
| `A092-A095_IMPLEMENTATION_CHECKLIST.md` | referenced by sibling A092 docs + `docs/a-series-wip-audit.md` + this plan | Move to `docs/archive/` |
| `A092-A095_QUICK_START.md` | referenced by sibling A092 docs + `docs/a-series-wip-audit.md` + this plan | Move to `docs/archive/` |
| `EXTRACTION_STATUS.md` | referenced by `backlog/A002.md`, `backlog/plans/A-002-...`, `backlog/plans/A-006-...`, this card/plan | Move to `docs/archive/` |
| `test_phoenix.py` | only referenced by this card/plan and one legacy plan mention | Delete as scratch script (not a pytest test module) |
| `campy.toml` vs `sidequests.toml` | both are loaded by runtime/config fallbacks (`arc_runtime/config.py`, `arc_runtime/runner_shell.py`, benchmark pre-submit/submission checks) | Keep both; no dead config file to delete/archive in this card |
| `backlog/.!48529!masterBacklogTracker.md` | stale duplicate | Delete after diff (confirmed stale copy) |

### Step 1: Inventory references (do this before touching anything)

```bash
for f in A092-A095_CODEBASE_SURVEY A092-A095_CODE_PATTERNS A092-A095_EXPLORATION_COMPLETE A092-A095_EXPLORATION_INDEX A092-A095_EXPLORATION_SUMMARY A092-A095_IMPLEMENTATION_CHECKLIST A092-A095_QUICK_START EXTRACTION_STATUS test_phoenix; do
  echo "== $f"; grep -rln "$f" --include="*.md" --include="*.py" --include="Makefile" . | grep -v __pycache__ | grep -v ".claude/worktrees" | grep -v "^./$f"
done
grep -rn "campy.toml\|sidequests.toml" --include="*.py" Makefile pyproject.toml | grep -v __pycache__
grep -rn "submission_results_single\|agent_execution_trace\|master_timeline" --include="*.py" --include="Makefile" . | grep -v __pycache__ | grep -v ".claude/worktrees" | head -40
```

Record each file's disposition in a table appended to this plan before executing.

### Step 2: Scratch docs

- Referenced by backlog/ARCHITECTURE → `git mv` to `docs/archive/` (create dir, add a one-line `docs/archive/README.md` saying these are historical exploration notes).
- Unreferenced → delete.
- Read `EXTRACTION_STATUS.md` (likely a one-time migration status) — archive unless ARCHITECTURE references it.

### Step 3: Artifact relocation

Introduce a single constant module (in `run_single_puzzle.py` or, post-A143, `arc_runtime/artifacts.py`):

```python
ARTIFACTS_DIR = Path(os.environ.get("ARC_ARTIFACTS_DIR", "artifacts"))
```

All writers build paths as `ARTIFACTS_DIR / "submission_results_single.live.jsonl"` etc.; `mkdir(parents=True, exist_ok=True)` once at startup. Update:
- writer constants in `run_single_puzzle.py`
- `run_review` `file://` URL builders (they embed absolute paths — derive from the same constants)
- `benchmarks/arc3/world_model_eval.py` reader if it hardcodes paths
- Makefile smoke targets that cat/inspect artifacts
- tests asserting paths (point them at tmp_path via `ARC_ARTIFACTS_DIR` env var — this also stops tests dirtying the repo root)

Move existing root artifacts into `artifacts/` (don't delete — they're the latest smoke evidence).

### Step 4: Git hygiene

`.gitignore` additions: `artifacts/`, `*.egg-info/`, `__pycache__/`, `*.live.jsonl`, root `submission_results*.json`, `agent_execution_trace.json`, `master_timeline.json`. Then:

```bash
git rm -r --cached arc_agi_campy.egg-info arc_agi_sidequests.egg-info __pycache__ 2>/dev/null
rm -rf arc_agi_campy.egg-info arc_agi_sidequests.egg-info __pycache__
```

(Check `git ls-files` first to see which are actually tracked.)

### Step 5: Stragglers

- `test_phoenix.py`: read it. Real test → `git mv` to `tests/test_phoenix.py` and ensure it passes/collects. Scratch → delete.
- `backlog/.!48529!masterBacklogTracker.md`: `diff` against `backlog/masterBacklogTracker.md`; if it contains no unique rows, delete. If it has rows missing from the real tracker (it predates recent edits), merge them first.
- `campy.toml`/`sidequests.toml`: from Step 1's grep, keep the one the config loader reads; delete the other (or shim-load both if code reads both — then add a follow-up note, don't fix here).

### Step 6: Verify

```bash
make test-a
PYTHONPATH=. .venv/bin/python run_single_puzzle.py --agent-version=v2 --num-puzzles 1 --max-steps 5
ls artifacts/                                # outputs landed there
git status --short | head                    # no regenerated junk at root
.venv/bin/python -m pytest tests/test_a088_compact_smoke_artifact_exports.py -q
```

## Files Modified

| File | Change |
|------|--------|
| root scratch docs | archive to `docs/archive/` or delete per inventory |
| `run_single_puzzle.py` / `arc_runtime/artifacts.py` | ARTIFACTS_DIR constant, path updates |
| `benchmarks/arc3/world_model_eval.py`, `Makefile` | path updates if referenced |
| `.gitignore` | artifact/egg-info/pycache patterns |
| `tests/*` | artifact-path assertions via env var |

## Conflict Note (for fan-out)

Artifact-path changes touch the same export code as A143 — land after A143 (then edits go in `arc_runtime/artifacts.py`), or before it with A143 rebasing. Scratch-doc/gitignore/straggler steps (2, 4, 5) are conflict-free and can land as a first standalone PR if A143 timing is unclear.

## Risks

- run_review `file://` links are consumed by the user to find results — verify the final_result row's URLs resolve after the move.
- Deleting a config twin the code secretly reads → Step 1 grep is mandatory; when ambiguous, archive instead of delete.
