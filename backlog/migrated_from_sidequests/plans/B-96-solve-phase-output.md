# B-96 — Solve Phase Output Visibility

## Metadata
- Card: B-96 (inline diagnostic, no separate backlog card needed)
- Priority: P0 (blocks live test interpretation)
- Dependencies: B95 (SolveEngine already exists)

## Summary

The SolveEngine runs every step and stores results in `orchestrator._solve_context`,
but this context is **never written to `_step_history`**. The output JSON therefore
has zero solve-phase visibility. This plan adds:

1. `solve_context` snapshot on each `_step_history` entry (orchestrator.py)
2. Live console logging from `solve()` each step
3. `solve_context` in `progress_log` per step (runner.py)
4. Top-level `solve_phase_summary` in result metadata (runner.py)
5. Console print of solve summary after each puzzle (run_single_puzzle.py)

## Technical Approach

### 1. `agents/arc3/orchestrator.py` — `act()` method

In the `_step_history.append({...})` dict (around line 328), add one key:

```python
"solve_context": dict(self._solve_context) if self._solve_context else None,
```

### 2. `agents/arc3/orchestrator.py` — `solve()` method

After setting `self._solve_context` (around line 222), add a logger.info line:

```python
archetype = self._solve_context["archetype"]
conf = self._solve_context["archetype_confidence"]
victory = (self._solve_context.get("victory_condition") or {}).get("type", "unknown")
chunk = (self._solve_context.get("active_chunk") or {}).get("description", "none")
dissonance = self._solve_context.get("dissonance", False)
logger.info(
    "[SOLVE] step=%d archetype=%s(%.2f) victory=%s chunk=%s dissonance=%s",
    step, archetype, conf, victory, chunk[:40] if chunk else "none", dissonance,
)
```

### 3. `agents/arc3/runner.py` — `_submission_row_from_result()`

In the `progress_log.append({...})` dict, add:

```python
"solve_context": step.get("solve_context"),
```

Also add a `solve_phase_summary` section. After building `progress_log`, compute:

```python
# Collect unique archetype/victory evolution across steps
archetypes_seen = []
victories_seen = []
final_solve_ctx = None
for s in debug_steps or []:
    sc = s.get("solve_context")
    if sc:
        a = sc.get("archetype", "unknown")
        if not archetypes_seen or archetypes_seen[-1] != a:
            archetypes_seen.append(a)
        v = (sc.get("victory_condition") or {}).get("type", "unknown")
        if not victories_seen or victories_seen[-1] != v:
            victories_seen.append(v)
        final_solve_ctx = sc

solve_phase_summary = {
    "archetype_evolution": archetypes_seen,
    "victory_evolution": victories_seen,
    "final_archetype": final_solve_ctx.get("archetype") if final_solve_ctx else "unknown",
    "final_archetype_confidence": final_solve_ctx.get("archetype_confidence", 0.0) if final_solve_ctx else 0.0,
    "final_victory_condition": (final_solve_ctx.get("victory_condition") or {}).get("type", "unknown") if final_solve_ctx else "unknown",
    "final_victory_confidence": (final_solve_ctx.get("victory_condition") or {}).get("confidence", 0.0) if final_solve_ctx else 0.0,
    "final_strategy_summary": final_solve_ctx.get("strategy_summary", "") if final_solve_ctx else "",
    "dissonance_triggered": any(
        (s.get("solve_context") or {}).get("dissonance") for s in (debug_steps or [])
    ),
    "object_roles": final_solve_ctx.get("object_roles", {}) if final_solve_ctx else {},
}
```

Include `solve_phase_summary` in the returned dict (top-level key alongside
`progress_log`, `prompt_trace`, etc.).

Also add it to `metadata` in `_build_metadata()`:

```python
if data.get("solve_phase_summary"):
    metadata["solve_phase_summary"] = data["solve_phase_summary"]
```

### 4. `agents/arc3/runner.py` — pass solve_phase_summary through result_payload

In `run()` method, after `result_payload = asdict(task_result)`, add:
```python
result_payload["solve_phase_summary"] = {}  # will be filled by _submission_row_from_result
```

The `_submission_row_from_result` already builds it — just make sure the top-level
key is returned in the dict (step 3 above handles this).

### 5. `run_single_puzzle.py` — print solve summary to console

In the result summary loop (around line 206), after logging steps, add:

```python
solve_summary = result.get("solve_phase_summary") or result.get("metadata", {}).get("solve_phase_summary") or {}
if solve_summary:
    logger.info(f"  [SOLVE] archetype: {solve_summary.get('final_archetype')} ({solve_summary.get('final_archetype_confidence', 0):.0%})")
    logger.info(f"  [SOLVE] victory: {solve_summary.get('final_victory_condition')} ({solve_summary.get('final_victory_confidence', 0):.0%})")
    logger.info(f"  [SOLVE] strategy: {solve_summary.get('final_strategy_summary', '')[:80]}")
    logger.info(f"  [SOLVE] dissonance: {solve_summary.get('dissonance_triggered')}")
    if solve_summary.get("archetype_evolution"):
        logger.info(f"  [SOLVE] archetype evolution: {' → '.join(solve_summary['archetype_evolution'])}")
```

## Files to Modify

- `agents/arc3/orchestrator.py` — add solve_context to _step_history entries + logger.info in solve()
- `agents/arc3/runner.py` — add solve_context to progress_log, add solve_phase_summary
- `run_single_puzzle.py` — print solve summary per puzzle

## No new tests required
This is output-only plumbing. Existing tests must still pass.

## Validation Commands

```bash
pytest -q tests/test_arc3_orchestrator.py tests/test_arc3_solver.py tests/test_arc3_hypothesis.py
```

All existing tests must pass. Then run live:

```bash
export ARC_API_KEY="$(python3 -c 'import json; print(json.load(open("benchmarks/.arc/arc.json"))["key"])')"
.venv/bin/python run_single_puzzle.py --real-api --num-puzzles 1 --card-id b96_solve_output_v1
```

Verify:
- Console shows `[SOLVE]` lines per step
- `submission_results_single.json` contains `solve_phase_summary` at top level
- Each entry in `progress_log` has a `solve_context` key
