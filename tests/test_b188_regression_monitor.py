import json
import time
from dataclasses import asdict

from benchmarks.arc3.regression_monitor import RegressionMonitor, RunRecord


def _mk_run(run_id: str, solve_rate: float = 0.9, avg_cost: float = 0.1, crash_rate: float = 0.0, judge_score: float = 4.0, model: str = "m1"):
    metrics = {
        "effectiveness": {"solve_rate": solve_rate, "judge_score_avg": judge_score},
        "efficiency": {"avg_cost_per_solve": avg_cost},
        "robustness": {"crash_rate": crash_rate},
    }
    return RunRecord(
        run_id=run_id,
        timestamp=time.time(),
        model=model,
        config_hash="cfg",
        git_commit="abc123",
        metrics=metrics,
    )


def test_no_comparison_when_less_than_three(tmp_path):
    history_dir = str(tmp_path)
    monitor = RegressionMonitor(history_dir=history_dir)

    # Save only two historical runs
    monitor.save_run(_mk_run("r1", solve_rate=0.9))
    monitor.save_run(_mk_run("r2", solve_rate=0.88))

    current = _mk_run("cur", solve_rate=0.7)
    alerts = monitor.check(current)
    assert alerts == []


def test_solve_rate_regression_flagged(tmp_path):
    history_dir = str(tmp_path)
    monitor = RegressionMonitor(history_dir=history_dir)

    # Create three baseline runs averaging ~0.9
    monitor.save_run(_mk_run("r1", solve_rate=0.9))
    monitor.save_run(_mk_run("r2", solve_rate=0.88))
    monitor.save_run(_mk_run("r3", solve_rate=0.92))

    # Current run drops to 0.75 (>10% absolute drop from ~0.9)
    current = _mk_run("cur", solve_rate=0.75)
    alerts = monitor.check(current)
    names = [a.metric_name for a in alerts]
    assert "solve_rate" in names


def test_no_false_positive_small_fluctuation(tmp_path):
    history_dir = str(tmp_path)
    monitor = RegressionMonitor(history_dir=history_dir)

    monitor.save_run(_mk_run("r1", solve_rate=0.80))
    monitor.save_run(_mk_run("r2", solve_rate=0.79))
    monitor.save_run(_mk_run("r3", solve_rate=0.81))

    current = _mk_run("cur", solve_rate=0.77)
    alerts = monitor.check(current)
    # small fluctuation; no alerts
    assert all(a.metric_name != "solve_rate" for a in alerts)


def test_metadata_and_multiple_alerts(tmp_path):
    history_dir = str(tmp_path)
    monitor = RegressionMonitor(history_dir=history_dir)

    # Baseline: healthy
    monitor.save_run(_mk_run("r1", solve_rate=0.9, avg_cost=0.1, crash_rate=0.01))
    monitor.save_run(_mk_run("r2", solve_rate=0.92, avg_cost=0.12, crash_rate=0.02))
    monitor.save_run(_mk_run("r3", solve_rate=0.91, avg_cost=0.11, crash_rate=0.015))

    # Current run: solve drops, cost increases >50%, crash increases >5%
    current = _mk_run("cur", solve_rate=0.7, avg_cost=0.3, crash_rate=0.12)
    alerts = monitor.check(current)
    names = sorted({a.metric_name for a in alerts})
    assert "solve_rate" in names
    assert "avg_cost_per_solve" in names
    assert "crash_rate" in names

    # Ensure the history file contains saved runs
    loaded = monitor._load_history()
    assert len(loaded) == 3
