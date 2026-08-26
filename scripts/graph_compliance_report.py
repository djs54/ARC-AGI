#!/usr/bin/env python
"""Reduce one or more ARC v2 trace files into Shift-A/Shift-C compliance
rates. Usage: python scripts/graph_compliance_report.py trace1.json [trace2.json ...]"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_HISTORY_PATH = Path("reports/compliance_history.jsonl")


def load_steps(path: Path) -> list[dict]:
    """Load step snapshots from a trace file. Returns empty list on any error."""
    steps = []
    try:
        with path.open() as f:
            trace_data = json.load(f)
    except Exception:
        # Non-fatal: this is a reporting tool, not a gate. Return empty if unreadable.
        return steps
    # trace_data is a JSON array of snapshots (confirmed by check_compliance_violations.py implementation)
    if isinstance(trace_data, list):
        for row in trace_data:
            if isinstance(row, dict) and row.get("snapshot_type") == "step":
                steps.append(row)
    return steps


def report(steps: list[dict]) -> dict:
    total = len(steps)
    if total == 0:
        return {"total_steps": 0}
    llm_goal = sum(1 for s in steps if s.get("reasoning_escalation_count"))
    llm_plan = sum(1 for s in steps if s.get("llm_escalated_plan"))
    grounded = sum(1 for s in steps if s.get("graph_grounded"))
    informed = sum(1 for s in steps if s.get("graph_informed"))
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
        "graph_informed_decision_rate": round(100 * informed / total, 2),
        "capability_missing_total": cap_missing,
        "compliance_violation_total": violations,
        "exhaustion_source_breakdown": exhaustion_sources,
    }


def append_history(report_dict: dict, trace_paths: list[str], history_path: Path) -> None:
    """Append a report snapshot to the history file (JSON Lines format)."""
    history_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trace_files": trace_paths,
        **report_dict,
    }
    with history_path.open("a") as f:
        f.write(json.dumps(row) + "\n")


def show_history(history_path: Path, last: int | None) -> int:
    """Print the last N history rows (or all if last is None)."""
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
            f"informed={row.get('graph_informed_decision_rate')}  "
            f"cap_missing={row.get('capability_missing_total')}  "
            f"violations={row.get('compliance_violation_total')}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reduce one or more ARC v2 trace files into Shift-A/Shift-C compliance rates."
    )
    parser.add_argument("traces", nargs="*", help="trace file paths")
    parser.add_argument(
        "--append-history",
        nargs="?",
        const=str(DEFAULT_HISTORY_PATH),
        default=None,
        help="append this report to a history file (default: %(default)s)",
    )
    parser.add_argument(
        "--show-history",
        nargs="?",
        const=str(DEFAULT_HISTORY_PATH),
        default=None,
        help="print history rows instead of computing a new report",
    )
    parser.add_argument(
        "--last",
        type=int,
        default=None,
        help="with --show-history, only show the last N rows",
    )

    args = parser.parse_args()

    # Handle --show-history mode (mutually exclusive with trace-file mode)
    if args.show_history is not None:
        return show_history(Path(args.show_history), args.last)

    # Handle normal report mode
    if not args.traces:
        print("usage: graph_compliance_report.py trace1.json [trace2.json ...]", file=sys.stderr)
        return 0  # Still report-only: return 0 even on usage error

    all_steps = []
    for trace_path in args.traces:
        all_steps.extend(load_steps(Path(trace_path)))

    report_dict = report(all_steps)
    print(json.dumps(report_dict, indent=2))

    # Optionally append to history file if --append-history was passed
    if args.append_history is not None:
        append_history(report_dict, args.traces, Path(args.append_history))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
