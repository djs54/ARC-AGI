# Plan: A153 — World-Model Live Telemetry Silently Stale When `--world-model-eval` Is Off

## Context

`arc_runtime/runner_shell.py::SingleTaskRunner.reset_live_output()` (current lines 244-248):

```python
def reset_live_output(self):
    self.live_output_path.write_text("")
    if self.world_model_eval:
        self.world_model_evaluator.reset()
        self.world_model_live_output_path.write_text("")
```

The truncation of `world_model_live_output_path` is nested inside `if self.world_model_eval:` — so it **only** resets when the eval flag is on. When it's off (the default — `self.world_model_eval = False` at `runner_shell.py:163`), the file is left completely untouched from whatever the last `--world-model-eval` run wrote, with no timestamp or run-identity marker distinguishing it from the current run's other fresh artifacts.

Confirmed in the wild: `artifacts/submission_results_single.world_model.live.jsonl` (mtime May 19) sits next to `artifacts/submission_results_single.live.jsonl` (mtime June 24) — both presented via the same `run_review.artifact_urls` dict (`arc_runtime/artifacts.py::build_run_review`, ~line 153) as if both belong to the same run.

## Implementation Steps

### Step 1: Always reset the file, regardless of eval flag

Change `reset_live_output()`:

```python
def reset_live_output(self):
    self.live_output_path.write_text("")
    if self.world_model_eval:
        self.world_model_evaluator.reset()
        self.world_model_live_output_path.write_text("")
    else:
        self.world_model_live_output_path.parent.mkdir(parents=True, exist_ok=True)
        self.world_model_live_output_path.write_text(
            json.dumps({"world_model_eval": False, "note": "world-model telemetry disabled for this run; pass --world-model-eval to enable"}) + "\n"
        )
```

Confirm `json` is already imported in `runner_shell.py` (check top-of-file imports; if not, add `import json`).

### Step 2: Confirm `reset_live_output` actually runs at the start of every run

Grep `reset_live_output(` call sites in `runner_shell.py` and `run_single_puzzle.py` to confirm it's invoked unconditionally during `initialize()` (not gated behind `real_api` or similar) — the file inspected in the Problem section was from a mock/real run mix, so this must fire on every invocation path, not just real-API ones.

### Step 3: Make the "disabled" state visible in `run_review` too

In `arc_runtime/artifacts.py::build_run_review` (current ~line 153, where `submission_results_single_world_model_live` is added to `artifact_urls`), check whether `runner.world_model_eval` is accessible at that call site (it takes `results_path`, various output paths — check if a `world_model_eval: bool` param needs threading through, or if it can read `result.get("world_model_eval")` if that's already stamped into the result dict elsewhere). If threading a new bool through is invasive, the Step 1 marker-row approach alone is sufficient — a human or agent opening the file sees `{"world_model_eval": false, ...}` immediately instead of unrelated old data. Prefer the minimal Step 1 fix over expanding `build_run_review`'s signature unless a test in Step 5 shows the marker-row approach isn't discoverable enough.

### Step 4: Same treatment in `run_single_puzzle.py` if it has its own runner class

Post-A143 decomposition, confirm whether `run_single_puzzle.py` still has an independent runner with its own `world_model_live_output_path` initialization/reset logic, or whether it fully delegates to `arc_runtime/runner_shell.py::SingleTaskRunner`. If independent, mirror Step 1 there too.

### Step 5: Tests

New file `tests/test_a153_world_model_telemetry_staleness.py`:

1. `test_reset_with_eval_enabled_truncates_and_resets_evaluator` — `runner.world_model_eval = True`, pre-seed the file with old content, call `reset_live_output()`, assert file is empty and `world_model_evaluator.reset()` was called (mock/spy).
2. `test_reset_with_eval_disabled_writes_marker_not_stale_content` — pre-seed the file with old JSONL content simulating a prior real run, `runner.world_model_eval = False`, call `reset_live_output()`, assert the file's single line parses as JSON with `world_model_eval: false` and does NOT contain any of the pre-seeded old content.
3. `test_two_runs_eval_then_disabled_no_bleed_through` — simulate run 1 (`world_model_eval=True`, write some step rows via `append_live_snapshot`), then run 2 (`world_model_eval=False`, call `reset_live_output()`) — assert run 2's file has no run-1 rows.
4. `test_run_review_reflects_disabled_state` — only if Step 3's threading is implemented; otherwise skip/remove this test and rely on tests 1-3.

### Step 6: Manual verification

```bash
PYTHONPATH=. .venv/bin/python run_single_puzzle.py --agent-version=v2 --num-puzzles 1 --max-steps 3 --world-model-eval
cat artifacts/submission_results_single.world_model.live.jsonl | head -3   # real rows
PYTHONPATH=. .venv/bin/python run_single_puzzle.py --agent-version=v2 --num-puzzles 1 --max-steps 3   # no flag
cat artifacts/submission_results_single.world_model.live.jsonl            # single disabled-marker line, not run 1's rows
```

## Verify

```bash
.venv/bin/python -m pytest tests/test_a153_world_model_telemetry_staleness.py -q
make test-a
make test-all
```

## Files Modified

| File | Change |
|------|--------|
| `arc_runtime/runner_shell.py` | `reset_live_output()` always resets the world-model-live file; writes a disabled-marker row when `world_model_eval` is False |
| `run_single_puzzle.py` | Mirror if it has independent runner init (check post-A143 state first) |
| `arc_runtime/artifacts.py` | Optional: thread `world_model_eval` into `run_review.artifact_urls` presentation (Step 3, only if needed) |
| `tests/test_a153_world_model_telemetry_staleness.py` | New, 3-4 tests |

## Risks

- Low risk — this is a write-path change to a diagnostic artifact only, no effect on agent decision-making or the evaluation loop.
- Don't conflate with the separate question (noted in the card) of whether `--world-model-eval` should default on for real-API runs — that's a behavior default the user should decide explicitly, not something this card should quietly change.
