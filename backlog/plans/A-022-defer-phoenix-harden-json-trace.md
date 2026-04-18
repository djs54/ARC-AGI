# A-022 - Defer Phoenix, Harden the JSON Trace as Primary Diagnostic

## Card metadata

- Card: A022
- Priority: P1
- Layer: evaluation/harness
- Depends on: A014, A016

## Summary

Stop treating Phoenix as the primary diagnostic surface. Make its auto-enable path best-effort so a broken localhost:6006 no longer aborts a smoke run, make the JSON trace crash-durable via atexit + atomic rename + fsync, and document the jq recipes that every post-smoke diagnosis has needed this week. The Phoenix shim and A016 auto-enable stay in place for operators who want them, but development velocity stops being gated on OTEL edge cases.

## Implementation approach

### 1. Soften the Phoenix auto-enable failure path

In `run_single_puzzle.py`, locate `_enforce_observability_preflight` (around lines 119–156 as of A016). The function currently ends with something like:

```python
obs = build_observability(config)
if not obs.enabled:
    endpoint = str(obs_cfg.get("endpoint", "http://127.0.0.1:6006/v1/traces"))
    raise RuntimeError(
        "Observability preflight failed: tracing could not be initialized.\n"
        f"python_executable={sys.executable}\n"
        f"endpoint={endpoint}\n"
        "Fix: verify dependencies are installed in this interpreter and that Phoenix is reachable."
    )
```

Wrap both the call and the `enabled` check in a branch-aware try/except. Track whether the enable was explicit (user set `PHOENIX_ENABLE` pre-call, or `config["observability"]["enabled"]` was explicitly True) vs. auto (A016 set it when user left it unset):

```python
auto_enabled = os.environ.get("_A016_AUTO_ENABLED_PHOENIX") == "1"

try:
    obs = build_observability(config)
    if not obs.enabled:
        raise RuntimeError(
            "Observability preflight failed: tracing could not be initialized."
        )
except Exception as exc:
    if auto_enabled:
        logger.warning(
            "Phoenix auto-enable failed (%s); falling back to JSON-trace only. "
            "Set PHOENIX_ENABLE=1 explicitly to make this fatal.",
            exc,
        )
        os.environ.pop("PHOENIX_ENABLE", None)
        if isinstance(config.get("observability"), dict):
            config["observability"]["enabled"] = False
        return
    raise
```

In the A016 auto-enable block, before setting `PHOENIX_ENABLE`, also set `os.environ["_A016_AUTO_ENABLED_PHOENIX"] = "1"` as the sentinel. Clear it at the end of the function in a `finally` if the enable path completed cleanly. This sentinel is a private internal signal — document it with a one-line comment.

Update the A016 tests in `tests/test_observability.py` to continue passing. Add the new test:

```python
def test_preflight_auto_enable_soft_fails_on_phoenix_unreachable(monkeypatch):
    """A022: auto-enable path must not raise when Phoenix cannot initialize."""
    from run_single_puzzle import _enforce_observability_preflight
    import importlib.util as _iu
    import sidequest_mcp_client.observability as obs_mod

    monkeypatch.delenv("PHOENIX_ENABLE", raising=False)

    def fake_find_spec(name):
        if name in ("opentelemetry", "phoenix", "phoenix.otel"):
            return object()
        return None
    monkeypatch.setattr(_iu, "find_spec", fake_find_spec)

    class _Broken:
        enabled = False
    monkeypatch.setattr(obs_mod, "build_observability", lambda cfg: _Broken())

    cfg = {"llm": {}}
    _enforce_observability_preflight(cfg)  # must NOT raise

    assert cfg.get("observability", {}).get("enabled") is False
    assert "PHOENIX_ENABLE" not in os.environ


def test_preflight_explicit_enable_still_hard_fails(monkeypatch):
    """A022: an explicit PHOENIX_ENABLE=1 must still raise on Phoenix failure."""
    from run_single_puzzle import _enforce_observability_preflight
    import sidequest_mcp_client.observability as obs_mod

    monkeypatch.setenv("PHOENIX_ENABLE", "1")

    class _Broken:
        enabled = False
    monkeypatch.setattr(obs_mod, "build_observability", lambda cfg: _Broken())

    cfg = {"observability": {"enabled": True}}
    with pytest.raises(RuntimeError):
        _enforce_observability_preflight(cfg)
```

### 2. Atomic JSON dumps with fsync

Add a small helper at module scope in `run_single_puzzle.py`:

```python
def _atomic_dump_json(path: Path, obj) -> None:
    """Write JSON atomically: write to <path>.tmp, fsync, then os.replace into place.

    A022: prevents truncated or partial trace files when the process is killed
    mid-write. os.replace is atomic on POSIX when src and dst are on the same
    filesystem (they are here — both sit under REPO_ROOT).
    """
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
```

Replace the three dump sites:

- `run_single_puzzle.py:319` — `json.dump(self.results, f, indent=2)` → `_atomic_dump_json(self.final_output_path, self.results)` (refactor the surrounding `with open(...)` away)
- `run_single_puzzle.py:435` — `json.dump(call_timeline, f, indent=2)` → `_atomic_dump_json(self.call_timeline_path, call_timeline)` (or whatever the actual path variable is)
- `run_single_puzzle.py:447-448` — the `agent_execution_trace` dump → `_atomic_dump_json(self.agent_execution_trace_path, agent_execution_trace)`
- `run_single_puzzle.py:565` — the master timeline dump → `_atomic_dump_json(self.master_timeline_path, master_timeline)`

### 3. atexit handler for crash durability

Add a new method `_atexit_flush_trace` to `ARCRunner`:

```python
def _atexit_flush_trace(self) -> None:
    """A022: write any in-flight execution trace to disk before interpreter exit.

    Fires on both normal termination and unhandled exceptions. Idempotent —
    the normal run() export also rewrites the file, so a subsequent normal
    completion overwrites whatever we dumped here.
    """
    try:
        snapshot = getattr(self, "_current_trace_snapshot", None) or []
        if not snapshot:
            return
        _atomic_dump_json(self.agent_execution_trace_path, list(snapshot))
        logger.info(
            "A022 atexit: flushed %d trace events to %s",
            len(snapshot),
            self.agent_execution_trace_path,
        )
    except Exception:
        # atexit handlers must never raise
        pass
```

In `ARCRunner.__init__` (around line 215 where the path attributes are set), add:

```python
self._current_trace_snapshot: list = []
```

Register the handler in `ARCRunner.run()` before the main task loop:

```python
atexit.register(self._atexit_flush_trace)
```

Add `import atexit` at the top of `run_single_puzzle.py`.

### 4. Stream trace events into the snapshot

The atexit handler can only flush what is reachable. Currently `_execution_trace` lives on the orchestrator; we need it to also be reachable from the runner without coupling the two modules deeper.

Simplest approach — in `ARCRunner.run()`, inside the loop that iterates tasks and awaits the orchestrator, after the orchestrator is constructed, assign:

```python
# A022: expose the orchestrator's in-flight trace so atexit can flush it.
self._current_trace_snapshot = getattr(orchestrator, "_execution_trace", [])
```

Because Python list references are live, the runner's snapshot attribute will see any `.append()` the orchestrator makes. No deep copy, no callback. When the next task constructs a new orchestrator, rebind the attribute. At the end of `run()` (normal-path finalizer), before the existing export, also clear the snapshot (so the atexit handler, if it fires on shutdown after normal completion, is a no-op):

```python
self._current_trace_snapshot = []
```

### 5. docs/trace_recipes.md

Create the file with the five canonical recipes. Each recipe is a fenced code block with the one-liner and a brief prose caption.

```markdown
# ARC_AGI trace recipes

Canonical jq recipes for `agent_execution_trace.json` and the sibling JSONL
outputs produced by `run_single_puzzle.py`. All recipes assume you run them
from the repo root and that the listed file exists from a recent smoke.

## 1. Every REPLAN route and why it went there (A017)

```sh
jq '.[]
    | select(.event_type == "phase_transition" and .metadata.reason == "replan_exit")
    | {step: .step, to: .metadata.target_phase, why: .metadata.route_reason}' \
  agent_execution_trace.json
```

## 2. Plateau family churn — distinct locked families in order (A018)

```sh
jq '[.[]
      | select(.event_type == "solve_plateau_detection")
      | .metadata.locked] | unique' \
  agent_execution_trace.json
```

## 3. Distinct plan_ids across the run — upper bound on chunk churn

```sh
jq '[.[]
      | select(.tool == "register_plan")
      | .result.plan_id] | unique | length' \
  agent_execution_trace.json
```

## 4. Phase violations — count and offending phases (A014)

```sh
jq '[.[]
      | select(.event_type == "phase_violation")] | length' \
  agent_execution_trace.json
```

## 5. Coverage-saturation signal — step at which saturation first fires (A010/A015)

```sh
jq 'first(.[]
           | select(.event_type == "graduation_assessment"
                    and (.metadata.reason | test("coverage_saturated")))
           | .step)' \
  agent_execution_trace.json
```

## 6. Per-step reward ticks — where the agent actually made progress

```sh
jq '.[]
    | select(.event_type == "action_outcome" and (.metadata.score_delta // 0) > 0)
    | {step: .step, action: .metadata.action_id, score_delta: .metadata.score_delta}' \
  submission_results_single.live.jsonl
```
```

### 6. Trace-durability tests

Create `tests/test_trace_durability.py`:

```python
import json
import os
import pytest
from pathlib import Path

from run_single_puzzle import _atomic_dump_json


def test_atomic_dump_round_trips(tmp_path):
    target = tmp_path / "trace.json"
    payload = [{"step": 1, "event": "start"}, {"step": 2, "event": "end"}]
    _atomic_dump_json(target, payload)
    assert target.exists()
    assert json.loads(target.read_text()) == payload
    # No leftover .tmp file
    assert not (tmp_path / "trace.json.tmp").exists()


def test_atomic_dump_leaves_no_partial_on_exception(tmp_path, monkeypatch):
    target = tmp_path / "trace.json"
    target.write_text('[{"step":0}]')  # pre-existing good file
    original_dump = json.dump

    def raising_dump(*args, **kwargs):
        raise IOError("disk full")

    monkeypatch.setattr(json, "dump", raising_dump)

    with pytest.raises(IOError):
        _atomic_dump_json(target, [{"step": 1}])

    # The prior good content must still be intact
    assert json.loads(target.read_text()) == [{"step": 0}]
    # The .tmp file may exist but must not have replaced the target
    monkeypatch.setattr(json, "dump", original_dump)


def test_atexit_flushes_in_flight_trace(tmp_path, monkeypatch):
    """A022: verify the atexit handler shape — it reads _current_trace_snapshot
    and atomic-dumps it to agent_execution_trace_path."""
    from run_single_puzzle import ARCRunner
    runner = ARCRunner.__new__(ARCRunner)
    runner.agent_execution_trace_path = tmp_path / "agent_execution_trace.json"
    runner._current_trace_snapshot = [{"step": 7, "event": "crash_before_export"}]
    runner._atexit_flush_trace()
    assert runner.agent_execution_trace_path.exists()
    loaded = json.loads(runner.agent_execution_trace_path.read_text())
    assert loaded == [{"step": 7, "event": "crash_before_export"}]
```

## Concrete file additions/edits

- edit `run_single_puzzle.py`:
  - add `import atexit`
  - add module-scope `_atomic_dump_json(path, obj)` helper
  - in `_enforce_observability_preflight`: set `_A016_AUTO_ENABLED_PHOENIX` sentinel in the A016 auto-enable branch; wrap the final `build_observability` check in a branch-aware try/except that warns-and-returns on the auto-enable path and re-raises on the explicit path
  - in `ARCRunner.__init__`: add `self._current_trace_snapshot: list = []`
  - in `ARCRunner.run()`: register `atexit.register(self._atexit_flush_trace)` before the task loop; after each orchestrator is constructed, rebind `self._current_trace_snapshot = orchestrator._execution_trace`; at normal end of `run()` after export, clear `self._current_trace_snapshot = []`
  - replace the four `json.dump(..., f, indent=2)` sites at lines ~319, ~435, ~447, ~565 with `_atomic_dump_json(path, obj)` calls
  - add `_atexit_flush_trace` method on `ARCRunner`
- edit `sidequest_mcp_client/observability.py`: no code change; the soft-fail path lives entirely in the entrypoint
- edit `ARCHITECTURE.md`: add one-line pointer to `docs/trace_recipes.md` and a note that Phoenix is best-effort in the auto-enable mode
- add `docs/trace_recipes.md` with six canonical recipes
- add `tests/test_trace_durability.py` with three tests
- extend `tests/test_observability.py` with the two new tests from step 1

## API/interface changes

- No public API changes.
- Side-effect change: `_enforce_observability_preflight` no longer raises when the A016 auto-enable path's `build_observability` returns `enabled=False`. Explicit opt-in behavior is unchanged.
- Side-effect change: `ARCRunner` gains one public-ish attribute `_current_trace_snapshot` (a reference into the orchestrator's trace list). Treat as private; other code should not write to it.
- Side-effect change: JSON output files now pass through `<path>.tmp` briefly during writes. Consumers that watch these paths with inotify/fswatch will see a brief temp-file appearance and a rename; normal read-after-write remains atomic from the consumer's perspective.

## Tests to add or run

- `pytest -q tests/test_observability.py` — covers the soft-fail / explicit-fail split
- `pytest -q tests/test_trace_durability.py` — new file, three tests
- Regression sweep: `pytest -q -k "observability or trace or preflight"`

## Validation commands

- `pytest -q tests/test_observability.py tests/test_trace_durability.py`
- Manual crash-durability verification:
  1. Temporarily add `raise RuntimeError("smoke crash test")` inside the main solve loop, a few steps in.
  2. Run the smoke.
  3. Confirm it aborts with the raised exception.
  4. Confirm `agent_execution_trace.json` exists, is valid JSON, and contains at least the events up to the crash step.
  5. Remove the injected raise.
- Manual Phoenix-unreachable verification:
  1. Ensure `phoenix` and `opentelemetry` are importable in the smoke interpreter.
  2. Ensure no Phoenix process is listening on `127.0.0.1:6006`.
  3. Run the smoke with neither `PHOENIX_ENABLE` set nor `[observability]` in config.
  4. Confirm the smoke completes, not aborts. Confirm the log contains `Phoenix auto-enable failed (...); falling back to JSON-trace only.`
  5. Re-run with `PHOENIX_ENABLE=1` set. Confirm the smoke aborts at preflight.

## Assumptions/defaults

- `os.replace` is atomic on both macOS (darwin) and Linux when source and destination live on the same filesystem. All four JSON output paths sit under `REPO_ROOT`, so this holds.
- `atexit` handlers fire on normal interpreter exit and on `sys.exit()` / unhandled exception exits, but NOT on `os._exit()`, `SIGKILL`, or a C-level crash. This is acceptable — SIGKILL durability would require a separate tail-writer architecture and is out of scope.
- `orchestrator._execution_trace` is a live `list` that the orchestrator appends to in-place. The snapshot attribute on `ARCRunner` is a reference, not a copy — appends are observed immediately by atexit. If that invariant ever changes (e.g., the orchestrator starts swapping the list for a new one), A022 needs a small follow-up to install a callback or deepcopy on a timer.
- `_A016_AUTO_ENABLED_PHOENIX` is an internal sentinel env var. Documented inline; operators should never set it.
- `tests/test_observability.py` already exists from A014/A016. This card extends it; no file creation.
- Phoenix is not removed, not deprecated, not uninstalled. Operators who prefer the UI can still run it and set `PHOENIX_ENABLE=1`. This card only changes the default posture from "Phoenix is required for a successful smoke" to "Phoenix is a bonus surface — the JSON trace is the contract."
