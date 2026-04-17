"""Cross-run regression detection for ARC benchmark runs.

Implements a simple rolling-3-run comparison and emits structured alerts
when configured metrics degrade beyond card-specified thresholds.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass
class RunRecord:
    run_id: str
    timestamp: float
    model: str
    config_hash: str
    git_commit: str
    metrics: Dict[str, Any]  # QualityDimensions from B182


@dataclass
class RegressionAlert:
    metric_name: str
    current_value: Optional[float]
    baseline_value: Optional[float]
    delta: Optional[float]
    delta_pct: Optional[float]
    severity: str  # "warning" or "critical"


class RegressionMonitor:
    """Load historical run records, compare a current run, and produce alerts.

    History is stored as newline-delimited JSON at ``{history_dir}/regression_history.jsonl``.
    """

    HISTORY_FILENAME = "regression_history.jsonl"

    def __init__(self, history_dir: str = "benchmarks/results"):
        self.history_dir = history_dir
        os.makedirs(self.history_dir, exist_ok=True)
        self.history_path = os.path.join(self.history_dir, self.HISTORY_FILENAME)

    def save_run(self, run: RunRecord) -> None:
        """Append a run record to the history file (JSONL)."""
        with open(self.history_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(run), default=str) + "\n")

    def _load_history(self) -> List[RunRecord]:
        if not os.path.exists(self.history_path):
            return []

        runs: List[RunRecord] = []
        with open(self.history_path, "r", encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    data = json.loads(ln)
                except Exception:
                    continue

                # Backwards-tolerant construction: metrics may be nested under benchmark_metrics
                metrics = data.get("metrics")
                if metrics is None:
                    bm = data.get("benchmark_metrics") or {}
                    metrics = bm.get("quality_dimensions") or {}

                runs.append(
                    RunRecord(
                        run_id=str(data.get("run_id", "")),
                        timestamp=float(data.get("timestamp", time.time())),
                        model=str(data.get("model", "")),
                        config_hash=str(data.get("config_hash", "")),
                        git_commit=str(data.get("git_commit", "")),
                        metrics=metrics or {},
                    )
                )

        return runs

    def _get_numeric(self, metrics: Dict[str, Any], path: List[str]) -> Optional[float]:
        cur: Any = metrics
        for p in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(p)
        if cur is None:
            return None
        try:
            return float(cur)
        except Exception:
            return None

    def _rolling_average(self, runs: List[RunRecord]) -> Dict[str, float]:
        """Average the key metrics across the provided runs.

        Returns a dict with keys: solve_rate, avg_cost_per_solve, crash_rate, judge_score_avg
        Missing numeric values are ignored in averaging (treated as absent).
        """
        vals = {
            "solve_rate": [],
            "avg_cost_per_solve": [],
            "crash_rate": [],
            "judge_score_avg": [],
        }

        for r in runs:
            m = r.metrics or {}
            s = self._get_numeric(m, ["effectiveness", "solve_rate"]) or self._get_numeric(m, ["solve_rate"]) or None
            c = self._get_numeric(m, ["efficiency", "avg_cost_per_solve"]) or self._get_numeric(m, ["avg_cost_per_solve"]) or None
            cr = self._get_numeric(m, ["robustness", "crash_rate"]) or self._get_numeric(m, ["crash_rate"]) or None
            j = self._get_numeric(m, ["effectiveness", "judge_score_avg"]) or self._get_numeric(m, ["judge_score_avg"]) or None

            if s is not None:
                vals["solve_rate"].append(s)
            if c is not None:
                vals["avg_cost_per_solve"].append(c)
            if cr is not None:
                vals["crash_rate"].append(cr)
            if j is not None:
                vals["judge_score_avg"].append(j)

        def avg(lst: List[float]) -> float:
            return float(sum(lst) / len(lst)) if lst else 0.0

        return {
            "solve_rate": avg(vals["solve_rate"]),
            "avg_cost_per_solve": avg(vals["avg_cost_per_solve"]),
            "crash_rate": avg(vals["crash_rate"]),
            "judge_score_avg": avg(vals["judge_score_avg"]),
        }

    def _format_pct(self, val: float) -> str:
        try:
            return f"{val:+.1f}%"
        except Exception:
            return "N/A"

    def check(self, current_run: RunRecord) -> List[RegressionAlert]:
        """Compare current_run against rolling-3-run average and return alerts.

        If fewer than 3 historical runs exist, returns an empty list.
        """
        history = self._load_history()
        if len(history) < 3:
            return []

        baseline = self._rolling_average(history[-3:])
        alerts: List[RegressionAlert] = []

        # Helpers to read current values
        cm = current_run.metrics or {}
        cur_solve = self._get_numeric(cm, ["effectiveness", "solve_rate"]) or self._get_numeric(cm, ["solve_rate"]) or None
        cur_cost = self._get_numeric(cm, ["efficiency", "avg_cost_per_solve"]) or self._get_numeric(cm, ["avg_cost_per_solve"]) or None
        cur_crash = self._get_numeric(cm, ["robustness", "crash_rate"]) or self._get_numeric(cm, ["crash_rate"]) or None
        cur_judge = self._get_numeric(cm, ["effectiveness", "judge_score_avg"]) or self._get_numeric(cm, ["judge_score_avg"]) or None

        # Solve rate regression: drop > 10%
        b_solve = baseline.get("solve_rate", 0.0)
        if cur_solve is not None and cur_solve < (b_solve - 0.10):
            delta = cur_solve - b_solve
            delta_pct = (delta / b_solve * 100.0) if b_solve != 0 else float("inf")
            severity = "critical" if abs(delta_pct) >= 20.0 else "warning"
            alerts.append(RegressionAlert("solve_rate", cur_solve, b_solve, delta, delta_pct, severity))

        # Avg cost per puzzle increases > 50%
        b_cost = baseline.get("avg_cost_per_solve", 0.0)
        if cur_cost is not None and b_cost is not None and b_cost > 0 and cur_cost > b_cost * 1.5:
            delta = cur_cost - b_cost
            delta_pct = (delta / b_cost * 100.0) if b_cost != 0 else float("inf")
            severity = "critical" if delta_pct >= 100.0 else "warning"
            alerts.append(RegressionAlert("avg_cost_per_solve", cur_cost, b_cost, delta, delta_pct, severity))

        # Crash rate increases > 5%
        b_crash = baseline.get("crash_rate", 0.0)
        if cur_crash is not None and cur_crash > (b_crash + 0.05):
            delta = cur_crash - b_crash
            delta_pct = (delta / b_crash * 100.0) if b_crash != 0 else float("inf")
            severity = "critical" if delta_pct >= 100.0 else "warning"
            alerts.append(RegressionAlert("crash_rate", cur_crash, b_crash, delta, delta_pct, severity))

        # Judge score avg drops > 1.0 point
        b_judge = baseline.get("judge_score_avg", 0.0)
        if cur_judge is not None and b_judge is not None and cur_judge < (b_judge - 1.0):
            delta = cur_judge - b_judge
            delta_pct = (delta / b_judge * 100.0) if b_judge != 0 else float("inf")
            severity = "critical" if abs(delta) >= 2.0 else "warning"
            alerts.append(RegressionAlert("judge_score_avg", cur_judge, b_judge, delta, delta_pct, severity))

        return alerts

    def alerts_to_json(self, alerts: List[RegressionAlert]) -> str:
        return json.dumps([asdict(a) for a in alerts], indent=2)
