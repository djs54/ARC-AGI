# Plan for B188 — Cross-Run Regression Detection

## Card Metadata

- **Card ID**: B188
- **Priority**: P2
- **Dependencies**: B182 (enhanced metrics)

## Summary

Track metrics across runs over time. Flag regressions when solve rate drops >10% vs rolling 3-run average. Store per-run metadata (model, config hash, commit).

## Technical Approach

### Step 1: Create benchmarks/arc3/regression_monitor.py

```python
@dataclass
class RunRecord:
    run_id: str
    timestamp: float
    model: str
    config_hash: str
    git_commit: str
    metrics: dict  # QualityDimensions from B182

@dataclass
class RegressionAlert:
    metric_name: str
    current_value: float
    baseline_value: float
    delta: float
    delta_pct: float
    severity: str  # "warning" or "critical"

class RegressionMonitor:
    def __init__(self, history_dir: str = "benchmarks/results"):
        self.history_dir = history_dir

    def check(self, current_run: RunRecord) -> List[RegressionAlert]:
        history = self._load_history()
        if len(history) < 3:
            return []  # Not enough data to compare

        baseline = self._rolling_average(history[-3:])
        alerts = []

        # Solve rate regression
        if current_run.metrics["effectiveness"]["solve_rate"] < baseline["solve_rate"] - 0.10:
            alerts.append(RegressionAlert("solve_rate", ...))

        # Cost regression
        if current_run.metrics["efficiency"].get("avg_cost_per_solve", 0) > baseline.get("avg_cost", 0) * 1.5:
            alerts.append(RegressionAlert("avg_cost_per_solve", ...))

        # Crash rate regression
        if current_run.metrics["robustness"]["crash_rate"] > baseline.get("crash_rate", 0) + 0.05:
            alerts.append(RegressionAlert("crash_rate", ...))

        # Judge score regression
        judge_avg = current_run.metrics["effectiveness"].get("judge_score_avg")
        baseline_judge = baseline.get("judge_score_avg")
        if judge_avg and baseline_judge and judge_avg < baseline_judge - 1.0:
            alerts.append(RegressionAlert("judge_score_avg", ...))

        return alerts

    def save_run(self, run: RunRecord):
        # Append to history file
        ...

    def _load_history(self) -> List[RunRecord]:
        # Load from history_dir
        ...

    def _rolling_average(self, runs: List[RunRecord]) -> dict:
        # Average metrics across runs
        ...
```

### Step 2: Generate run metadata

In runner.py, after batch completes:

```python
import hashlib, subprocess
config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]
git_commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()

run_record = RunRecord(
    run_id=f"run_{int(time.time())}",
    timestamp=time.time(),
    model=config["llm"]["model"],
    config_hash=config_hash,
    git_commit=git_commit,
    metrics=quality_dimensions,
)
monitor = RegressionMonitor()
alerts = monitor.check(run_record)
monitor.save_run(run_record)

if alerts:
    logger.warning("REGRESSION DETECTED: %s", json.dumps([asdict(a) for a in alerts]))
```

### Step 3: Tests

Create `tests/test_b188_regression_monitor.py`:
1. Test regression flagged when solve rate drops 15%
2. Test no false positive when metrics fluctuate within 5%
3. Test < 3 historical runs → empty alerts (no crash)
4. Test config_hash and git_commit captured correctly
5. Test multiple alerts (solve rate + crash rate both regress)

## Verification

```bash
pytest tests/test_b188_regression_monitor.py -v
```
