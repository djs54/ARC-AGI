# B-57-arc-harness — ARC A/B Harness (Baseline vs SideQuests-Augmented)

**Card:** B57 | **Priority:** P12 | **Depends on:** B55, B56 (adapter + serializer complete)

## Summary
Build A/B harness for ARC-AGI-3. Runs fixed puzzle subset with baseline and SideQuests-augmented modes to measure memory impact.

## Technical Approach

### Two Modes

1. **Baseline:** 
   - No passive ingestion
   - No memory retrieval
   - Direct episode loop

2. **Augmented:**
   - Passive ingestion via notify_turn
   - Memory retrieval via current_truth between episodes
   - Working memory load tracking

### Infrastructure
- Fixed seed for reproducibility
- Task manifest with checksums
- Per-puzzle tracing (action sequence, rewards, errors)
- Metrics: solve_rate, steps_per_solve, repeated_invalid_actions, token_cost

### Configuration
```yaml
arc3_harness:
  task_set: "evaluation_set"  # 100 puzzles
  seed: 42
  baseline_model: "llama2-7b"
  time_limit_per_puzzle: 120s
  max_attempts_per_puzzle: 10
```

## Files to Create/Modify

- `benchmarks/arc3/harness.py` — A/B runner
- `benchmarks/arc3/tasks_manifest.json` — puzzle list + checksums
- `tests/test_arc3_harness.py` — harness behavior validation

## Acceptance Criteria

1. Baseline and augmented runs produce different action sequences (memory affects decisions)
2. Fixed-seed runs are deterministic
3. Metrics are computed and exported in standardized format
4. Token tracking is accurate
5. Results can be compared statistically
6. Full harness run completes in reasonable time (e.g., <1 hour for 100 puzzles with 10s/puzzle model)

## Notes

- B57 instantiates generic A/B contract (B48) for ARC
- Most critical benchmark for demonstrating SideQuests value
