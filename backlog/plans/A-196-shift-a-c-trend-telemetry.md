# Plan: A196 — Trend Shift-A/Shift-C Compliance Rates Across Runs

## Card metadata

- ID: A196
- Priority: P2
- Layer: ARC runtime
- Dependencies: A192, A194, A195

## Summary

Add cheap per-step telemetry fields (mostly reading data already present in candidate/execution metadata) plus one new instrumentation point (`graph_queries.py::_call_tool`, the single choke point every graph call already passes through) for a `capability_missing` counter, then a standalone aggregator script reducing trace files into trended compliance rates for Shift A ("wakes up to investigate, never to detect") and Shift C ("graph decides, not RAG").

## Technical approach

### 1. `graph_queries.py::_call_tool` — count `capability_missing` degradations

Read `graph_queries.py::_call_tool` in full before editing (current structure has two separate `except Exception as exc:` blocks — one around the initial sync call, one around the awaited-result branch — each independently able to produce `{"status": "capability_missing", ...}`). Add an instance counter, initialized in `__init__`:

```python
self._capability_missing_count: int = 0
```

Increment it at both places `_call_tool` currently returns the `capability_missing` dict (non-strict branch only — the `strict` branch raises instead and should not increment this counter, since it never actually degrades):

```python
if self._is_missing_tool_error(exc, tool_name):
    if self.strict:
        raise RuntimeError(f"required ARC tool missing: {tool_name}") from exc
    self._capability_missing_count += 1
    return {"status": "capability_missing", "tool": tool_name, "error": str(exc)}
```

Add a public method:

```python
def pop_capability_missing_count(self) -> int:
    """Returns the capability_missing count accumulated since the last call,
    then resets it -- gives telemetry a natural per-step delta with no
    bookkeeping needed on the telemetry side."""
    count = self._capability_missing_count
    self._capability_missing_count = 0
    return count
```

### 2. `telemetry.py::_step_snapshot` — new fields

Read `telemetry.py` in full before editing to confirm current line numbers and exactly how `execution`/`plan`/`evaluation`/`state` are already threaded into `_step_snapshot` (the method already has all of these in scope — see `telemetry.py:133-140`).

```python
llm_escalated_plan = False
graph_grounded = False
if execution is not None and execution.candidate is not None and isinstance(execution.candidate.metadata, Mapping):
    cand_meta = execution.candidate.metadata
    llm_escalated_plan = bool(cand_meta.get("llm_guidance"))
    graph_evidence = cand_meta.get("graph_evidence")
    graph_grounded = bool(graph_evidence) or bool(cand_meta.get("entity_neighborhood_grounded"))

exhaustion_source = None
if evaluation is not None and isinstance(evaluation.metadata, Mapping):
    exhaustion_source = evaluation.metadata.get("exhaustion_source")

capability_missing_count = 0
if self._graph_query_port is not None:
    pop = getattr(self._graph_query_port, "pop_capability_missing_count", None)
    if pop is not None:
        try:
            capability_missing_count = pop()
        except Exception:
            capability_missing_count = 0
```

Confirm whether `ArcV2Telemetry` currently holds a reference to the graph port at all — if it doesn't (check `telemetry.py`'s `__init__`/dataclass fields, e.g. `game_title`/`game_tags`/etc. at the top of the file), this card needs to add one (`graph_query_port: GraphQueryPort | None = None` as a new dataclass field, threaded in from wherever `ArcV2Telemetry` is constructed) — do not assume it's already wired without checking.

Add all four new keys to the `snapshot = {...}` dict literal (`telemetry.py:153-187`):

```python
"llm_escalated_plan": llm_escalated_plan,
"graph_grounded": graph_grounded,
"exhaustion_source": exhaustion_source,
"capability_missing_count": capability_missing_count,
```

Note `graph_evidence.get("entity_neighborhood_grounded")` above is a placeholder key name for A192's eventual contribution — confirm the exact metadata key A192 actually uses (read A192's implementation once it lands, or its plan if implementing this card first) rather than guessing; if A192 hasn't landed yet, `graph_grounded` should still work correctly using only the pre-existing `graph_evidence`/rule-confidence signal, and be extended once A192's actual field name is known.

### 3. `scripts/graph_compliance_report.py` (new)

```python
#!/usr/bin/env python
"""Reduce one or more ARC v2 trace files into Shift-A/Shift-C compliance
rates. Usage: python scripts/graph_compliance_report.py trace1.json [trace2.json ...]"""
import json
import sys
from pathlib import Path


def load_steps(path: Path) -> list[dict]:
    steps = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("snapshot_type") == "step":
                steps.append(row)
    return steps


def report(steps: list[dict]) -> dict:
    total = len(steps)
    if total == 0:
        return {"total_steps": 0}
    llm_goal = sum(1 for s in steps if s.get("reasoning_escalation_count"))
    llm_plan = sum(1 for s in steps if s.get("llm_escalated_plan"))
    grounded = sum(1 for s in steps if s.get("graph_grounded"))
    cap_missing = sum(s.get("capability_missing_count", 0) for s in steps)
    violations = sum(s.get("compliance_violation_count", 0) for s in steps)
    exhaustion_sources: dict[str, int] = {}
    for s in steps:
        src = s.get("exhaustion_source")
        if src:
            exhaustion_sources[src] = exhaustion_sources.get(src, 0) + 1
    return {
        "total_steps": total,
        "llm_escalation_rate_goal_per_100": round(100 * llm_goal / total, 2),
        "llm_escalation_rate_plan_per_100": round(100 * llm_plan / total, 2),
        "graph_grounded_decision_rate": round(100 * grounded / total, 2),
        "capability_missing_total": cap_missing,
        "compliance_violation_total": violations,
        "exhaustion_source_breakdown": exhaustion_sources,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: graph_compliance_report.py trace1.json [trace2.json ...]", file=sys.stderr)
        return 2
    all_steps = []
    for arg in sys.argv[1:]:
        all_steps.extend(load_steps(Path(arg)))
    print(json.dumps(report(all_steps), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Confirm the trace file's actual line format (JSON-lines vs. a single array) against `run_single_puzzle.py`/`telemetry.py`'s real `append_snapshot` wiring before implementing `load_steps` — the sketch above assumes JSON-lines per A195's plan's same assumption; verify once, reuse the same confirmed format in both A195's and this card's scripts rather than re-deriving it twice.

### 4. `docs/trace_recipes.md`

Add a new section (matching the doc's existing format — read it first to match style) documenting each new field with a one-line description and a jq recipe, e.g.:

```
# LLM escalation rate (plan-tier)
jq -s '[.[] | select(.snapshot_type=="step")] | (map(select(.llm_escalated_plan)) | length) / length * 100' agent_execution_trace.json
```

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/graph_queries.py` | `_call_tool`: count `capability_missing` degradations; new `pop_capability_missing_count()` |
| `agents/arc4/telemetry.py` | `_step_snapshot`: 4 new fields; possibly a new `graph_query_port` field on `ArcV2Telemetry` if not already present |
| `scripts/graph_compliance_report.py` (new) | Aggregator |
| `docs/trace_recipes.md` | New section for the new fields |
| `tests/test_a196_shift_a_c_trend_telemetry.py` (new) | Coverage (see Tests) |

## Tests

New `tests/test_a196_shift_a_c_trend_telemetry.py`:

1. `_call_tool` increments the capability_missing counter exactly once per degraded call, across both the sync and awaited-result exception paths; `pop_capability_missing_count()` returns the accumulated total and resets to `0`; a second immediate call returns `0`.
2. `_call_tool` in `strict` mode does not increment the counter (it raises instead).
3. `_step_snapshot` sets `llm_escalated_plan=True` when `execution.candidate.metadata={"llm_guidance": True}`, `False` otherwise.
4. `_step_snapshot` sets `graph_grounded=True` when `execution.candidate.metadata["graph_evidence"]` is non-empty, `False` when empty/absent.
5. `_step_snapshot` sets `exhaustion_source` from `evaluation.metadata.get("exhaustion_source")` when present, `None` otherwise.
6. `_step_snapshot` sets `capability_missing_count` from `pop_capability_missing_count()` when a graph port is present, `0` when absent or the method is missing.
7. `scripts/graph_compliance_report.py::report()` on a constructed fixture list of step dicts produces the correct rates for all four metrics and the correct `exhaustion_source_breakdown` counts.
8. `report()` on an empty step list returns `{"total_steps": 0}` without dividing by zero.

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a196_shift_a_c_trend_telemetry.py -v
.venv/bin/python -m pytest tests/test_observability.py tests/test_trace_durability.py -v
make test-a
make test-all
make smoke && python scripts/graph_compliance_report.py <resulting trace path>
```

## Assumptions/defaults

- `graph_grounded`'s exact derivation (which metadata keys count as "grounded") should be treated as a starting definition, not a final one — note in the Resolution which fields it actually checks, since A192 landing later may add a new key this card's `graph_grounded` derivation should also recognize (documented as a known follow-up, not blocking this card).
- If `ArcV2Telemetry` doesn't already hold a `graph_query_port` reference, adding one is in scope for this card (needed for `capability_missing_count`) — confirm during implementation, don't assume either way from this plan.
- This card's aggregator is a read-only reporting tool — it must never fail a build or exit non-zero on its own (unlike A195's `check_compliance_violations.py`, which is a pass/fail gate). This script reports trends; A195's script enforces an invariant. Keep the two separate, don't merge them.
