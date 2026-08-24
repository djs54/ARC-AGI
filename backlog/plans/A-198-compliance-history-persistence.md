# Plan: A198 — Persist Compliance Report Snapshots to a History File

## Card metadata

- ID: A198
- Priority: P2
- Layer: ARC runtime
- Dependencies: A196

## Summary

Extend A196's `scripts/graph_compliance_report.py` with an opt-in `--append-history` flag that writes each report as one JSONL row to a durable file, plus a `--show-history` read mode, so compliance rates can be watched over time instead of only inspected one run at a time.

## Technical approach

### 1. Read A196's actual `report()`/`main()` implementation first

Read the real (implemented, not this plan's earlier sketch) `scripts/graph_compliance_report.py` from A196 in full before editing — confirm its exact CLI argument handling (currently sketched as positional trace paths only) and the exact keys `report()` returns, since this card's history row schema should embed that dict as-is (`**report`) rather than redefining field names.

### 2. `--append-history [path]`

```python
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_HISTORY_PATH = Path("reports/compliance_history.jsonl")


def append_history(report: dict, trace_paths: list[str], history_path: Path) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trace_files": trace_paths,
        **report,
    }
    with history_path.open("a") as f:
        f.write(json.dumps(row) + "\n")
```

Wire into `main()` via `argparse` (replacing or extending whatever simple `sys.argv` handling A196 actually shipped with — read it first, adapt rather than assume):

```python
parser = argparse.ArgumentParser()
parser.add_argument("traces", nargs="*", help="trace file paths")
parser.add_argument("--append-history", nargs="?", const=str(DEFAULT_HISTORY_PATH), default=None,
                     help="append this report to a history file (default: %(default)s)")
parser.add_argument("--show-history", nargs="?", const=str(DEFAULT_HISTORY_PATH), default=None,
                     help="print history rows instead of computing a new report")
parser.add_argument("--last", type=int, default=None, help="with --show-history, only show the last N rows")
```

`--append-history` used bare (no value) defaults to `DEFAULT_HISTORY_PATH`; used with a value (`--append-history custom/path.jsonl`) overrides it — `nargs="?"` with `const=`/`default=` gives exactly this behavior.

### 3. `--show-history [path] [--last N]`

```python
def show_history(history_path: Path, last: int | None) -> int:
    if not history_path.exists():
        print(f"No history yet at {history_path}")
        return 0
    rows = [json.loads(line) for line in history_path.read_text().splitlines() if line.strip()]
    if last is not None:
        rows = rows[-last:]
    for row in rows:
        print(
            f"{row.get('timestamp')}  "
            f"llm_goal={row.get('llm_escalation_rate_goal_per_100')}  "
            f"llm_plan={row.get('llm_escalation_rate_plan_per_100')}  "
            f"grounded={row.get('graph_grounded_decision_rate')}  "
            f"cap_missing={row.get('capability_missing_total')}  "
            f"violations={row.get('compliance_violation_total')}"
        )
    return 0
```

When `--show-history` is passed, `main()` should call this and return immediately, skipping normal report generation entirely (mutually exclusive with the positional trace-file report mode — document this in `--help`, don't silently combine both).

### 4. `Makefile` — additive only

```makefile
compliance-history: ## record the latest smoke trace's compliance report into the trend history
	$(PYTHON) scripts/graph_compliance_report.py $(TRACE_PATH) --append-history
```

Confirm `TRACE_PATH`'s actual value/convention against A195/A196's implementations (both plans flagged this same open question — resolve it once, reuse the resolved answer here rather than re-deriving it a third time).

### 5. `docs/trace_recipes.md`

One new recipe, e.g.:

```
# Trend graph-grounded decision rate over the last 10 recorded runs
jq -s '.[-10:] | .[] | {timestamp, graph_grounded_decision_rate}' reports/compliance_history.jsonl
```

## Concrete file changes

| File | Change |
|------|--------|
| `scripts/graph_compliance_report.py` | `--append-history` and `--show-history` CLI modes |
| `Makefile` | New `compliance-history` target (additive only) |
| `docs/trace_recipes.md` | One new recipe for the history file |
| `tests/test_a198_compliance_history_persistence.py` (new) | Coverage (see Tests) |

## Tests

New `tests/test_a198_compliance_history_persistence.py`:

1. `append_history` on a fresh (nonexistent) path creates the file and parent directory, writes exactly one valid JSON line.
2. Calling `append_history` twice appends a second line without disturbing the first (read both back, confirm both parse and both are distinct/correct).
3. Every appended row's `trace_files` field matches what was passed in, and the rest of the row matches the `report` dict passed in (no silently dropped or renamed keys).
4. `show_history` on a nonexistent path prints a "no history yet" message and returns cleanly (no exception).
5. `show_history` with `--last 2` against a 5-row history file only shows the 2 most recent rows, in chronological order.
6. Running the script without `--append-history`/`--show-history` (plain trace-file report mode) produces identical stdout to before this card — regression guard confirming the new flags are additive.
7. `--show-history` and positional trace args together: confirm the documented mutually-exclusive behavior (history mode wins, or an explicit error — whichever `main()` actually implements; the test should match, not dictate, this plan's step 3 leaves the exact choice to implementation).

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a198_compliance_history_persistence.py -v
.venv/bin/python -m pytest tests/test_a196_shift_a_c_trend_telemetry.py -v
make test-a
make test-all
make smoke && make compliance-history && python scripts/graph_compliance_report.py --show-history
```

## Assumptions/defaults

- The history file format is JSON Lines specifically (one JSON object per line, append-only) — chosen because it's crash-safe to append to (no need to read-modify-rewrite a JSON array) and trivially `jq`-able, matching this repo's existing trace-file convention.
- `reports/compliance_history.jsonl`'s default location and whether `reports/` should be gitignored (this is generated, environment-specific data, not source) should be confirmed/decided during implementation — check whether a `reports/` or similar output directory convention already exists elsewhere in this repo before introducing a new one.
- This card does not build any visualization/dashboard — `--show-history`'s output is a plain compact line per row, intentionally minimal. A real dashboard, if ever wanted, is a separate future card, not folded in here.
