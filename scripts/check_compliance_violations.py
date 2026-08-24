#!/usr/bin/env python
"""Exit non-zero if any step in an ARC v2 trace recorded a Shift-B compliance
violation. Usage: python scripts/check_compliance_violations.py [trace_path]
Defaults to the most recently modified agent_execution_trace.json under the
artifacts directory if no path is given."""
import json
import os
import sys
from pathlib import Path


def main() -> int:
    trace_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _find_latest_trace()
    if trace_path is None or not trace_path.exists():
        print(f"No trace file found at {trace_path}", file=sys.stderr)
        return 2
    violations_found = []
    with trace_path.open() as f:
        trace_data = json.load(f)
    # trace_data is a JSON array of snapshots
    if isinstance(trace_data, list):
        for row in trace_data:
            if isinstance(row, dict):
                count = row.get("compliance_violation_count", 0)
                if count:
                    violations_found.append((row.get("step"), count))
    if violations_found:
        print(f"COMPLIANCE VIOLATIONS in {trace_path}:", file=sys.stderr)
        for step, count in violations_found:
            print(f"  step {step}: {count} violation(s)", file=sys.stderr)
        return 1
    print(f"No compliance violations in {trace_path}")
    return 0


def _find_latest_trace() -> Path | None:
    """Find agent_execution_trace.json in the artifacts dir. Matches
    arc_runtime/runner_shell.py's own ARTIFACTS_DIR resolution exactly
    (ARC_ARTIFACTS_DIR env var, falling back to <repo_root>/artifacts) so
    this script finds the same trace a real run actually wrote, including
    when ARC_ARTIFACTS_DIR overrides the default location."""
    repo_root = Path(__file__).resolve().parent.parent
    artifacts_dir = Path(os.environ.get("ARC_ARTIFACTS_DIR", repo_root / "artifacts"))
    trace_path = artifacts_dir / "agent_execution_trace.json"
    if trace_path.exists():
        return trace_path
    return None


if __name__ == "__main__":
    raise SystemExit(main())
