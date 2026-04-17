# Plan for B182 — Enhanced ABHarness Metrics: Four Quality Dimensions

## Card Metadata

- **Card ID**: B182
- **Priority**: P1
- **Dependencies**: B180 (cost data), B181 (judge verdicts), B185 (failure classes)

## Summary

Extend `_compute_metrics` in `ab_harness.py` to report four quality dimensions: effectiveness, efficiency, robustness, safety/alignment.

## Current State

### Existing metrics (ab_harness.py:~241)

The existing `_compute_metrics` computes: solve_rate, avg_steps_to_solve, token_efficiency, repeated_mistakes. These become part of the expanded set.

## Technical Approach

### Step 1: Define metrics structure

```python
@dataclass
class QualityDimensions:
    effectiveness: dict  # solve_rate, judge_score_avg, near_miss_rate, judge_by_archetype
    efficiency: dict     # avg_steps_per_solve, avg_tokens_per_solve, avg_cost_per_solve, tokens_per_step
    robustness: dict     # crash_rate, timeout_rate, budget_exceeded_rate, strategy_exhausted_rate, loop_stuck_rate
    alignment: dict      # invalid_action_rate, dissonance_trigger_rate
```

### Step 2: Extend _compute_metrics (ab_harness.py)

```python
def _compute_metrics(self, results: List[ABTaskResult]) -> QualityDimensions:
    total = len(results)
    solved = [r for r in results if r.correct]
    failed = [r for r in results if not r.correct]

    # Effectiveness
    judge_scores = [r.judge_verdict["composite_score"] for r in results if r.judge_verdict]
    near_misses = [r for r in failed if r.judge_verdict and r.judge_verdict.get("composite_score", 0) >= 3.0]
    effectiveness = {
        "solve_rate": len(solved) / total if total else 0,
        "judge_score_avg": sum(judge_scores) / len(judge_scores) if judge_scores else None,
        "near_miss_rate": len(near_misses) / total if total else 0,
    }

    # Efficiency
    solved_steps = [r.steps for r in solved]
    solved_tokens = [r.tokens_input + r.tokens_output for r in solved]
    solved_costs = [r.cost_usd for r in solved if hasattr(r, 'cost_usd') and r.cost_usd is not None]
    efficiency = {
        "avg_steps_per_solve": sum(solved_steps) / len(solved_steps) if solved_steps else None,
        "avg_tokens_per_solve": sum(solved_tokens) / len(solved_tokens) if solved_tokens else None,
        "avg_cost_per_solve": sum(solved_costs) / len(solved_costs) if solved_costs else None,
    }

    # Robustness (from failure_class)
    failure_classes = [r.failure_class for r in failed if r.failure_class]
    robustness = {
        "crash_rate": failure_classes.count("crash") / total if total else 0,
        "timeout_rate": failure_classes.count("llm_timeout") / total if total else 0,
        "budget_exceeded_rate": failure_classes.count("budget_exceeded") / total if total else 0,
        "strategy_exhausted_rate": failure_classes.count("strategy_exhausted") / total if total else 0,
        "loop_stuck_rate": failure_classes.count("stuck_in_loop") / total if total else 0,
    }

    # Alignment
    alignment = {
        "invalid_action_rate": sum(r.invalid_action_count or 0 for r in results) / max(sum(r.steps for r in results), 1),
        "dissonance_trigger_rate": sum(1 for r in results if r.dissonance_triggered) / total if total else 0,
    }

    return QualityDimensions(effectiveness, efficiency, robustness, alignment)
```

### Step 3: Extend ABComparison._compare_results

Compute delta for each metric: `sidequests_value - baseline_value`.

### Step 4: Graceful handling of missing data

When B180/B181/B185 haven't run (fields are None), report "N/A" for those metrics. Never crash.

### Step 5: Tests

Create `tests/test_b182_enhanced_metrics.py`:
1. Test effectiveness metrics with mix of solved/failed results
2. Test efficiency metrics with token/cost data
3. Test robustness metrics with various failure classes
4. Test graceful N/A when judge_verdict is None
5. Test ABComparison delta computation

## Verification

```bash
pytest tests/test_b182_enhanced_metrics.py -v
pytest tests/test_adapters.py -v  # regression
```
