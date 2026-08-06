# Plan: A143 — Decompose run_single_puzzle.py

## Context

`run_single_puzzle.py` (1,454 lines) regions, by current line ranges (re-verify before cutting; A137/A139 will have shifted them):

| Lines (~) | Content | Destination |
|-----------|---------|-------------|
| 1-60 | imports, logging setup | split across new modules |
| 100-250 | config helpers, LLM/observability preflight, OTEL setup | `arc_runtime/bundle.py` (or stay if CLI-ish) |
| 370-437 | `_ArcV2GameSession` (open/execute_action/close) | `arc_runtime/game_session.py` |
| 439-450 | `_unwrap_arc_game_payload` | `arc_runtime/game_session.py` |
| 452-500 | `build_arg_parser` | stays in `run_single_puzzle.py` |
| 500-565 | `_build_arc_v2_bundle` + phase wiring + snapshot hooks | `arc_runtime/bundle.py` |
| 565-665 | per-task v2 driver: session open, Temporal-vs-inline dispatch, result handling | `arc_runtime/dispatch.py` |
| 666-770 | `SingleTaskRunner.__init__` / `initialize` (config, MCP readiness, manifest) | stays (CLI runner shell) |
| 770-1110 | live snapshot appending, world-model eval rows, `_make_final_result_compact`, `_build_run_review`, artifact export | `arc_runtime/artifacts.py` |
| 1110-1454 | main loop, exports, `main()` | stays, slimmed |

`arc_runtime/` exists (169 LOC currently — check its contents and `__init__.py` before adding). MCP seam: runtime scope already covers `arc_runtime/`; new modules must not import `mcp_engine.*` / `campy.*` (they don't today; the moves preserve that).

## Implementation Steps

Strictly mechanical, one module per step, full test run between steps. **Do not refactor logic while moving** — same statements, new homes.

### Step 0: Inventory importers

```bash
grep -rn "from run_single_puzzle import\|import run_single_puzzle" --include="*.py" . | grep -v __pycache__
```

Tests import symbols from `run_single_puzzle` (e.g. `_unwrap_arc_game_payload`, `_make_final_result_compact` in `test_a088_compact_smoke_artifact_exports.py`, `test_a134_*`). Keep back-compat re-exports in `run_single_puzzle.py` for every symbol any test imports.

### Step 1: `arc_runtime/game_session.py`

Move `_ArcV2GameSession` → rename class to `ArcV2GameSession` (keep alias `_ArcV2GameSession = ArcV2GameSession` in run_single_puzzle.py), `_unwrap_arc_game_payload`, and A137's `_compute_progress`. Module needs: `httpx`, `ARC3Adapter`, `NoOpBrainClient` imports (from `benchmarks.arc3.adapter` — allowed: importing seam-exempt benchmarks code from runtime is the existing pattern; confirm `tests/test_import_boundary.py` only forbids `mcp_engine`/`campy`, not `benchmarks`). Run suite.

### Step 2: `arc_runtime/bundle.py`

Move `_build_arc_v2_bundle` and its helper dataclass/namedtuple (search `bundle =` / `class.*Bundle`). Public name `build_arc_v2_bundle` + re-export. Run suite.

### Step 3: `arc_runtime/dispatch.py`

Move the per-task driver block (session open → observation normalize → bundle build → Temporal-vs-inline選択 → result). Extract as `run_v2_task(task, runner, args, brain_client, card_id) -> dict`. The Temporal in-process worker setup (lines ~590-655) moves wholesale. Keep the `HAS_TEMPORAL` guard import pattern. Run suite + one mock-mode run.

### Step 4: `arc_runtime/artifacts.py`

Move: `append_live_snapshot` logic (the methods on `SingleTaskRunner` around lines 770-800 — extract as functions taking explicit paths/evaluator args, keep thin methods on the runner delegating), `_make_final_result_compact`, `_build_run_review`, `_json_dumps`, `_atomic_dump_json`, timeline/export helpers (lines ~960-1110). These are `@staticmethod`s already — the move is clean. Re-export `_make_final_result_compact` for tests. Run suite.

### Step 5: Slim the entrypoint

`run_single_puzzle.py` keeps: arg parser, `SingleTaskRunner` shell (init/initialize/main loop calling `dispatch.run_v2_task` and `artifacts.*`), `main()`, back-compat re-export block with a comment listing which tests need each symbol. Target <300 lines; if the runner shell pushes past, move `SingleTaskRunner` to `arc_runtime/runner_shell.py` too.

### Step 6: Schema-stability check

Before starting, capture a baseline: run mock mode once, save `submission_results_single.live.jsonl` key sets per snapshot_type:

```bash
python3 -c "import json;[print(d.get('snapshot_type'),sorted(d.keys())) for d in map(json.loads,open('submission_results_single.live.jsonl'))]" > /tmp/a143_before.txt
```

After Step 5, rerun and diff. Key sets must be identical.

### Step 7: Verify

```bash
make test-a
.venv/bin/python -m pytest tests/ -q -k "a088 or a134 or import_boundary or submission" \
  --ignore=tests/test_model_constraints.py
PYTHONPATH=. .venv/bin/python run_single_puzzle.py --agent-version=v2 --num-puzzles 1 --max-steps 10
wc -l run_single_puzzle.py   # < 300
```

Optionally one `--live-smoke` to confirm the real-API path end to end.

## Files Modified

| File | Change |
|------|--------|
| `arc_runtime/game_session.py` | New — session + unwrap + progress |
| `arc_runtime/bundle.py` | New — v2 wiring |
| `arc_runtime/dispatch.py` | New — Temporal/inline dispatch |
| `arc_runtime/artifacts.py` | New — exports/snapshots/reports |
| `run_single_puzzle.py` | Slim to CLI + re-exports |

## Conflict Note (for fan-out)

**Must land after A137 and A139** (both edit `run_single_puzzle.py`; this card moves the lines they touch). Do not run in parallel with anything that edits `run_single_puzzle.py`.

## Risks

- Hidden module-level state (loggers, OTEL init order) — preserve import order; initialize logging in the entrypoint only.
- Tests importing private names — covered by the re-export block; Step 0 inventory makes it exhaustive.
- Diagnostic log lines added 2026-06-11 (RAW/NORMALIZED available_actions) move with their code; keep them.
