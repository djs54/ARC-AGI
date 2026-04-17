# B-58-arc-model-strategy — Offline Model Strategy Card (Allowed Models + Resource Budget)

**Card:** B58 | **Priority:** P12 | **Depends on:** B54 (rules verified)

## Summary
Define approved local model matrix and resource budget for offline ARC submission. Select primary + fallback models within contest constraints.

## Technical Approach

### Model Profiling
Benchmark 3-5 candidate models under identical workload:
- **Candidates:** Llama 3.1 (8B), Llama 2 (7B), Mistral (7B), OpenELM, others
- **Metrics:** 
  - Solve quality on calibration set (10-20 puzzles)
  - Latency per step (target: <2s on M1 Pro)
  - Memory footprint (target: <13GB for 8GB GPU constraint)
  - Stability across long episodes

### Resource Budget
```yaml
contest_constraints:
  cpu_compute: "4 cores max"
  gpu_memory: "8GB max"
  wall_time_per_puzzle: "120s"
  offline_only: true

selected_models:
  primary: "llama3.1:8b-instruct-q5"   # best quality vs. latency
  fallback: "llama2:7b-q4"            # if primary OOMs
  reasoning_timeout: "5s per step"
```

## Files to Create/Modify

- `benchmarks/arc3/model_matrix.md` — profiling results + rationale
- `benchmarks/arc3/model_budget.yaml` — resource allocation
- `benchmarks/arc3/model_eval.py` — profiling harness
- `tests/test_model_constraints.py` — verify selected model meets budget

## Acceptance Criteria

1. At least 3 candidate models are profiled
2. Primary model meets verified runtime constraints (B54)
3. Fallback model is documented with trigger conditions
4. Profiling results are reproducible
5. Selected model choice is defensible (quality vs. cost tradeoff)

## Notes

- Contest rules require offline/local models only
- All models must be downloadable and cached locally
- Model selection is critical for ARC track credibility
