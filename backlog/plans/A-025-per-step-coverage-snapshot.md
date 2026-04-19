# A-025 - Fix A023 Per-Step Coverage Snapshot Emission

## Card metadata

- Card: A025
- Priority: P1
- Layer: ARC runtime
- Depends on: A023

## Summary

Relocate the A023 `exploration_coverage_snapshot` trace emission from `ARCOrchestrator.plan()` (which runs on PLAN phase entry, ~twice per 10-step run) to the PERCEIVE phase entry (which runs every step). Add a `_last_coverage_snapshot_step` guard so the event fires exactly once per distinct step number. Remove the old emission site. Close the A023 acceptance-criterion gap identified in the 2026-04-19 live smoke.

## Implementation approach

### 1. Confirm PERCEIVE fires per step

Before moving code, verify the invariant: PERCEIVE phase fires exactly once per runtime step. Grep the 2026-04-19 `agent_execution_trace.json` (or a fresh smoke trace) for `phase_start` events with `operation: "perceive"` and confirm the count equals the step count:

```sh
.venv/bin/python -c "
import json, collections
t=json.load(open('agent_execution_trace.json'))
c=collections.Counter()
for e in t:
    if e.get('event_type')=='phase_start':
        c[e.get('operation')] += 1
for k,v in c.most_common(): print(f'{v:4d}  {k}')
"
```

If PERCEIVE fires >1× per step in some code paths (e.g. REPLAN→PERCEIVE re-entry), the `_last_coverage_snapshot_step` guard handles it — the emit will fire on the first PERCEIVE for that step and skip the rest. If PERCEIVE is skipped entirely for some steps (unlikely but possible under REPLAN→MODEL short-circuits), add a fallback emit in the MODEL phase entry guarded by the same step counter.

### 2. Add the step-guard attribute

In `agents/arc3/orchestrator.py`, locate `ARCOrchestrator.__init__` (grep `class ARCOrchestrator`) and the existing A023 counter `self._untested_probes_forced_in_run: int = 0`. Add alongside:

```python
# A025: guard against duplicate coverage-snapshot emission within a single step.
# None means "no snapshot emitted yet this puzzle"; otherwise holds the last
# step number that emitted a snapshot.
self._last_coverage_snapshot_step: Optional[int] = None
```

Locate the per-puzzle reset hook (grep `_untested_probes_forced_in_run = 0` — whichever method resets it when a new puzzle starts). Add:

```python
self._last_coverage_snapshot_step = None
```

in the same place.

### 3. Add the per-step emission at PERCEIVE entry

Locate `ARCOrchestrator.perceive` (grep `async def perceive` in `agents/arc3/orchestrator.py`). Immediately after the phase_start trace and before any other work, add:

```python
# A025: emit per-step exploration coverage snapshot (relocated from plan()).
# The step counter guard prevents duplicate emission if PERCEIVE re-enters
# within the same runtime step (e.g., REPLAN→PERCEIVE loop).
try:
    step = len(self._step_history)
    if self._last_coverage_snapshot_step != step:
        coverage = (self._hypothesis_context or {}).get("action_coverage") or {}
        tested = sorted(
            a for a in getattr(self, "_available_actions", [])
            if a not in (coverage.get("untested_actions") or [])
        )
        untested = sorted(coverage.get("untested_actions") or [])
        self._emit_trace_event(
            "operation",
            "exploration_coverage_snapshot",
            {"step": step},
            {
                "tested": tested,
                "untested": untested,
                "initial_exploration_complete": bool(coverage.get("initial_exploration_complete")),
            },
        )
        self._last_coverage_snapshot_step = step
except Exception:
    pass
```

The `try/except` mirrors the original A023 emit's defensive shape — a trace-side failure must not break the step loop.

### 4. Remove the stale emission from `plan()`

In `agents/arc3/orchestrator.py` at lines 2120-2141, delete the entire `# A023: emit a per-step exploration coverage snapshot for auditability` block (the whole `try: coverage = ... except Exception: pass` block). The `draft_plan_steps` emit immediately above it stays.

### 5. Tests

Extend `tests/test_exploration_probing.py` with one new test:

```python
def test_coverage_snapshot_fires_once_per_step(monkeypatch):
    """A025: coverage snapshot must fire exactly once per distinct step number,
    even if PERCEIVE re-enters within a step (e.g., REPLAN loop)."""
    orch = _make_orchestrator(monkeypatch)
    orch._hypothesis_context = {
        "action_coverage": {"untested_actions": ["ACTION2", "ACTION3"]},
    }
    orch._available_actions = ["ACTION1", "ACTION2", "ACTION3"]
    orch._step_history = []

    emitted: list[dict] = []
    orch._emit_trace_event = lambda ev, op, details, result=None, elapsed=None: (
        emitted.append({"op": op, "step": (details or {}).get("step")})
        if op == "exploration_coverage_snapshot" else None
    )

    # Simulate 3 distinct steps, with step 1 re-entering PERCEIVE twice
    # (as happens in REPLAN→PERCEIVE loops).
    orch._step_history = ["s0"]
    orch._emit_coverage_snapshot()  # step 1
    orch._step_history = ["s0"]
    orch._emit_coverage_snapshot()  # step 1 re-entry — should be skipped
    orch._step_history = ["s0", "s1"]
    orch._emit_coverage_snapshot()  # step 2
    orch._step_history = ["s0", "s1", "s2"]
    orch._emit_coverage_snapshot()  # step 3

    steps = [e["step"] for e in emitted]
    assert steps == [1, 2, 3], f"expected one emit per distinct step, got {steps}"
```

The test assumes the emission body is extracted into a private helper method `_emit_coverage_snapshot()` on `ARCOrchestrator` — do that refactor as part of step 3 (the body in PERCEIVE becomes a one-line call `self._emit_coverage_snapshot()`). That makes the helper unit-testable without needing a full PERCEIVE phase fixture.

Existing A023 tests in `tests/test_exploration_probing.py` must continue to pass — the guard behavior (the `guard_untested_probe` emit) is independent of the coverage-snapshot emit site.

### 6. Documentation

In `ARCHITECTURE.md`, find the `#### Exploration-coverage policy (A023)` subsection (around line 276-283). Append one sentence:

```
The `exploration_coverage_snapshot` event fires once per step at PERCEIVE
phase entry, guarded by `_last_coverage_snapshot_step` to prevent duplicate
emission within a step (A025).
```

## Concrete file additions/edits

- edit `agents/arc3/orchestrator.py`:
  - add `self._last_coverage_snapshot_step: Optional[int] = None` in `__init__`
  - reset it to `None` wherever `_untested_probes_forced_in_run` is reset
  - extract the emission body into `_emit_coverage_snapshot(self) -> None` (new private method)
  - call `self._emit_coverage_snapshot()` from `perceive()` immediately after the phase_start trace
  - remove the emit block at lines 2120-2141 of `plan()`
- edit `tests/test_exploration_probing.py`:
  - add `test_coverage_snapshot_fires_once_per_step`
- edit `ARCHITECTURE.md`:
  - append one sentence to the `#### Exploration-coverage policy (A023)` subsection

## API/interface changes

- New private method: `ARCOrchestrator._emit_coverage_snapshot(self) -> None`. Not part of any public seam.
- New attribute: `ARCOrchestrator._last_coverage_snapshot_step: Optional[int]`. Test-visible; treat as read-only externally.
- No new trace event types (the event name stays `exploration_coverage_snapshot`).
- No MCP seam changes, no config changes.

## Tests to add or run

- `pytest -q tests/test_exploration_probing.py` (must include new `test_coverage_snapshot_fires_once_per_step`)
- `make test-a` (the A022–A024 suites — regression check)
- `pytest -q tests/test_orchestrator*.py` (regression check on the full orchestrator surface)

## Validation commands

1. Run `make smoke` from repo root (requires `SIDEQUESTS_MCP_CMD` set, Ollama running, brain daemon up — the Makefile target handles the env var).
2. Confirm the event count matches step count:
   ```sh
   .venv/bin/python -c "
   import json
   t=json.load(open('agent_execution_trace.json'))
   snaps=[e for e in t if e.get('operation')=='exploration_coverage_snapshot']
   print(f'snapshots: {len(snaps)}')
   print(f'distinct steps: {len(set(e[\"details\"][\"step\"] for e in snaps))}')
   print(f'step numbers: {sorted(e[\"details\"][\"step\"] for e in snaps)}')
   "
   ```
   For a 10-step run: `snapshots: 10`, `distinct steps: 10`, `step numbers: [0, 1, 2, ..., 9]` (or `[1..10]` depending on how `_step_history` is keyed).
3. Confirm no duplicate step numbers:
   ```sh
   .venv/bin/python -c "
   import json
   t=json.load(open('agent_execution_trace.json'))
   steps=[e['details']['step'] for e in t if e.get('operation')=='exploration_coverage_snapshot']
   assert len(steps) == len(set(steps)), f'duplicate step numbers: {steps}'
   print('OK — all unique')
   "
   ```

## Assumptions/defaults

- PERCEIVE phase fires once per runtime step in the canonical orchestrator driver. Verified in the 2026-04-19 smoke trace where `phase_start` events with `operation: "perceive"` correspond 1:1 with step counts. If this invariant breaks in a future refactor, the `_last_coverage_snapshot_step` guard still prevents duplicates — the emit just shifts to whichever phase runs first per step.
- `len(self._step_history)` is the canonical "current step index" accessor. Verified at `orchestrator.py:2129` where A023 already uses it.
- The emit-site relocation does not change the event schema (same `details`, same `result`). Downstream consumers (operator-facing jq recipes in `docs/trace_recipes.md`, test assertions) continue to work unchanged.
- No cost-tracker implications — the emit is a pure trace event, no MCP round-trip, no LLM call.
- The `_last_coverage_snapshot_step` guard is puzzle-scoped, not global. When a new puzzle starts the counter resets to `None`, so step 0 of the new puzzle emits correctly.
